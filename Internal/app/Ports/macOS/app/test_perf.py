#!/usr/bin/env python3
"""Tests for the perf/ package — InferenceProfiler and Diagnostics.

Run: python test_perf.py

Covers:
  • perf package import safety (no hard dependencies)
  • InferenceProfiler: timing, memory, CPU per stage
  • Diagnostics: enhanced RTF monitor, memory pressure, bottleneck ID
  • Profiling hooks integration with pipeline orchestrator
  • STT-to-Stage1 handoff profiling (VAL-CROSS-004)
  • Graceful degradation when optional deps (psutil, GPU) are absent

These tests use the real pipeline and profiler modules. If the pipeline
package is unavailable, pipeline-dependent tests are skipped gracefully.
"""

import sys
import os
import time

# Ensure the app directory is on the path so imports resolve properly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Test framework (stdlib, matches existing pattern)
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0
_skipped = 0


def _run(name, func):
    global _passed, _failed, _skipped
    try:
        func()
        _passed += 1
        print(f"  ok  {name}")
    except unittest.SkipTest:
        _skipped += 1
        print(f" SKIP {name}")
    except AssertionError as e:
        _failed += 1
        print(f" FAIL {name}: {e}")
    except Exception as e:
        _failed += 1
        print(f" FAIL {name}: {type(e).__name__}: {e}")


class unittest:
    """Minimal namespace so existing SkipTest patterns work."""
    class SkipTest(Exception):
        pass


def skip_if(condition, reason=""):
    if condition:
        raise unittest.SkipTest(reason)


# ===========================================================================
# 1. Import Safety
# ===========================================================================

def test_import_profiler():
    """perf.profiler imports cleanly without hard dependencies."""
    from perf import profiler as p
    assert hasattr(p, "InferenceProfiler"), "InferenceProfiler missing"
    assert callable(p.InferenceProfiler), "InferenceProfiler not callable"


def test_import_diagnostics():
    """perf.diagnostics imports cleanly without hard dependencies."""
    from perf import diagnostics as d
    assert hasattr(d, "Diagnostics"), "Diagnostics missing"
    assert callable(d.Diagnostics), "Diagnostics not callable"


def test_import_package():
    """perf package __init__ exports both profiler and diagnostics."""
    from perf import InferenceProfiler, Diagnostics, get_profiler, get_diagnostics
    assert callable(InferenceProfiler)
    assert callable(Diagnostics)
    assert callable(get_profiler)
    assert callable(get_diagnostics)


def test_package_singletons_match_submodule_singletons():
    """Cross-imports must share one profiler/diagnostics global state."""
    from perf import get_profiler as package_profiler
    from perf import get_diagnostics as package_diagnostics
    from perf.profiler import get_profiler as module_profiler
    from perf.diagnostics import get_diagnostics as module_diagnostics

    assert package_profiler() is module_profiler()
    assert package_diagnostics() is module_diagnostics()


# ===========================================================================
# 2. InferenceProfiler — Core Functionality
# ===========================================================================

def test_profiler_instantiation():
    """InferenceProfiler can be instantiated with default settings."""
    from perf.profiler import InferenceProfiler
    p = InferenceProfiler()
    assert p.enabled, "Profiler should be enabled by default"
    assert len(p.stage_timings) == 0, "Should start with empty timings"


def test_profiler_enable_disable():
    """InferenceProfiler can be enabled and disabled."""
    from perf.profiler import InferenceProfiler
    p = InferenceProfiler(enabled=False)
    assert not p.enabled
    p.enable()
    assert p.enabled
    p.disable()
    assert not p.enabled


def test_profiler_context_manager():
    """InferenceProfiler context manager (with block) measures latency."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("test_stage") as ctx:
        time.sleep(0.01)
    assert ctx.elapsed_ms > 0, "Stage timing should be > 0"
    assert ctx.stage_name == "test_stage"
    assert len(profiler.stage_timings) == 1


def test_profiler_multiple_stages():
    """Profiler records multiple stages in order."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("stage_one") as ctx1:
        time.sleep(0.01)
    with profiler.stage("stage_two") as ctx2:
        time.sleep(0.01)
    assert len(profiler.stage_timings) == 2
    assert profiler.stage_timings[0].stage_name == "stage_one"
    assert profiler.stage_timings[1].stage_name == "stage_two"


def test_profiler_stage_names():
    """Stage names are recorded correctly."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("pipeline_stage1_punctuation"):
        pass
    with profiler.stage("pipeline_stage2_grammar"):
        pass
    names = [s.stage_name for s in profiler.stage_timings]
    assert "pipeline_stage1_punctuation" in names
    assert "pipeline_stage2_grammar" in names


def test_profiler_disabled_skips_timing():
    """When disabled, stage context manager does NOT record timings."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler(enabled=False)
    with profiler.stage("should_not_record"):
        time.sleep(0.01)
    assert len(profiler.stage_timings) == 0, (
        "Disabled profiler should not record stage timings"
    )


def test_profiler_profile_pipeline():
    """profile_pipeline() measures timing per pipeline stage."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    result, profile = profiler.profile_pipeline(
        "hello world this is a test", lane="text"
    )
    assert isinstance(result, str), "Should return processed text"
    assert len(result) > 0
    assert isinstance(profile, dict), "Should return profile dict"
    assert "total_ms" in profile, "Total timing missing"
    assert "stages" in profile, "Stage breakdown missing"
    assert profile["total_ms"] > 0, "Total time should be > 0"


def test_profiler_profile_transcription():
    """profile_transcription() returns a timing breakdown dict."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    # Use a tiny fake audio segment for testing.
    import numpy as np
    fake_audio = np.zeros(int(16000 * 0.5), dtype=np.float32)  # 0.5s silence
    profile = profiler.profile_transcription(fake_audio, sample_rate=16000)
    assert isinstance(profile, dict), "Should return profile dict"
    assert "audio_duration_sec" in profile
    assert "stt_elapsed_ms" in profile
    assert profile["audio_duration_sec"] > 0


def test_profiler_hooks_enabled_flag():
    """Profiler hooks can be globally toggled."""
    from perf.profiler import InferenceProfiler, profiling_enabled, set_profiling_enabled
    original = profiling_enabled()
    try:
        set_profiling_enabled(False)
        assert not profiling_enabled()
        set_profiling_enabled(True)
        assert profiling_enabled()
    finally:
        set_profiling_enabled(original)


# ===========================================================================
# 3. InferenceProfiler — Memory / CPU Measurement
# ===========================================================================

def test_profiler_memory_measurement():
    """Profiler measures memory usage per stage when psutil is available."""
    from perf.profiler import InferenceProfiler
    try:
        import psutil
    except ImportError:
        raise unittest.SkipTest("psutil not installed")
    profiler = InferenceProfiler(track_memory=True)
    with profiler.stage("mem_test"):
        # Allocate a small list to bump memory slightly
        _x = list(range(10000))
    assert len(profiler.stage_timings) == 1
    timing = profiler.stage_timings[0]
    # Memory fields may be 0 if psutil isn't available at measurement time
    assert hasattr(timing, "mem_before_mb"), "mem_before_mb field missing"
    assert hasattr(timing, "mem_after_mb"), "mem_after_mb field missing"


def test_profiler_cpu_measurement():
    """Profiler measures CPU usage per stage when psutil is available."""
    from perf.profiler import InferenceProfiler
    try:
        import psutil
    except ImportError:
        raise unittest.SkipTest("psutil not installed")
    profiler = InferenceProfiler(track_cpu=True)
    with profiler.stage("cpu_test"):
        # Do some CPU work
        sum(range(10000))
    timing = profiler.stage_timings[0]
    assert hasattr(timing, "cpu_percent"), "cpu_percent field missing"


def test_profiler_gpu_measurement_optional():
    """GPU measurement is optional and degrades gracefully if unavailable."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler(track_gpu=True)
    # Should not crash even without GPU tools
    with profiler.stage("gpu_test"):
        pass
    timing = profiler.stage_timings[0]
    assert hasattr(timing, "gpu_info"), "gpu_info field missing"


# ===========================================================================
# 4. InferenceProfiler — Results / Reporting
# ===========================================================================

def test_profiler_summary_report():
    """Profiler produces a summary report dict."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("fast_stage"):
        pass
    with profiler.stage("slow_stage"):
        time.sleep(0.02)
    report = profiler.summary()
    assert isinstance(report, dict)
    assert "total_ms" in report
    assert "num_stages" in report
    assert "stages" in report
    assert report["num_stages"] == 2
    assert report["total_ms"] > 0


def test_profiler_clear():
    """Profiler.clear() resets all recorded timings."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("test"):
        pass
    assert len(profiler.stage_timings) == 1
    profiler.clear()
    assert len(profiler.stage_timings) == 0


def test_profiler_stt_to_stage1_handoff():
    """STT-to-Stage1 handoff is profiled specifically (VAL-CROSS-004)."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()

    # Simulate the STT-to-Stage1 handoff: mark STT end, then time Stage 1.
    with profiler.stage("stt_transcription"):
        pass  # STT would happen here

    with profiler.stage("stt_to_stage1_handoff"):
        # This is the gap between STT completing and Stage 1 starting
        pass

    with profiler.stage("pipeline_stage1_punctuation"):
        # Stage 1 runs here
        pass

    stages = [s.stage_name for s in profiler.stage_timings]
    assert "stt_to_stage1_handoff" in stages, (
        "STT-to-Stage1 handoff must be profiled (VAL-CROSS-004)"
    )
    # Verify the order: STT → handoff → Stage 1
    stt_idx = stages.index("stt_transcription")
    handoff_idx = stages.index("stt_to_stage1_handoff")
    stage1_idx = stages.index("pipeline_stage1_punctuation")
    assert stt_idx < handoff_idx < stage1_idx, (
        "Stages must be in order: STT → handoff → Stage 1 (VAL-CROSS-004)"
    )


# ===========================================================================
# 5. Diagnostics — RTF Monitor
# ===========================================================================

def test_diagnostics_instantiation():
    """Diagnostics can be instantiated."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    assert d is not None
    assert hasattr(d, "monitor_rtf")
    assert hasattr(d, "check_memory_pressure")


def test_diagnostics_enhanced_rtf():
    """Enhanced RTF monitor extends transcription.py RTF with per-stage breakdown."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    # Simulate a pipeline run with per-stage timings
    stage_timings = {
        "stt": 500,        # ms
        "stage1_punct": 50,
        "stage2_grammar": 1200,
        "stage3_format": 800,
        "stage4_cleanup": 5,
    }
    rtf_report = d.monitor_rtf(
        audio_duration_sec=5.0,
        total_processing_ms=sum(stage_timings.values()),
        stage_breakdown_ms=stage_timings,
    )
    assert isinstance(rtf_report, dict)
    assert "rtf" in rtf_report
    assert "verdict" in rtf_report
    assert "stage_breakdown" in rtf_report
    assert "slowest_stage" in rtf_report
    assert rtf_report["rtf"] > 0


def test_diagnostics_rtf_thresholds():
    """RTF thresholds match transcription.py conventions."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    # Fast: 1s processing for 10s audio → RTF 0.1
    r = d.monitor_rtf(10.0, 1000.0, {})
    assert r["verdict"] == "healthy"

    # OK: 4s processing for 5s audio → RTF 0.8
    r = d.monitor_rtf(5.0, 4000.0, {})
    assert r["verdict"] == "ok"

    # Marginal: 6s processing for 5s audio → RTF 1.2
    r = d.monitor_rtf(5.0, 6000.0, {})
    assert r["verdict"] == "marginal"

    # Slow: 9s processing for 5s audio → RTF 1.8
    r = d.monitor_rtf(5.0, 9000.0, {})
    assert r["verdict"] == "slow"

    # Very slow: 15s processing for 5s audio → RTF 3.0
    r = d.monitor_rtf(5.0, 15000.0, {})
    assert r["verdict"] == "very_slow"


def test_diagnostics_rtf_zero_audio():
    """Zero audio duration RTF is handled safely."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    r = d.monitor_rtf(0.0, 100.0, {})
    assert r["rtf"] == float("inf")
    assert r["verdict"] == "very_slow"


# ===========================================================================
# 6. Diagnostics — Memory Pressure Detection
# ===========================================================================

def test_diagnostics_memory_pressure():
    """Memory pressure detection returns a status dict."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    status = d.check_memory_pressure()
    assert isinstance(status, dict)
    assert "under_pressure" in status
    assert "free_memory_mb" in status
    assert "threshold_mb" in status
    assert isinstance(status["under_pressure"], bool)


def test_diagnostics_memory_pressure_threshold_configurable():
    """Memory pressure threshold can be configured."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics(memory_threshold_mb=1024)
    status = d.check_memory_pressure()
    assert status["threshold_mb"] == 1024

    d2 = Diagnostics(memory_threshold_mb=256)
    status2 = d2.check_memory_pressure()
    assert status2["threshold_mb"] == 256


def test_diagnostics_memory_warning():
    """Memory pressure warning includes a human-readable message."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics(memory_threshold_mb=999999)  # Very high threshold
    status = d.check_memory_pressure()
    # With a high threshold, we may or may not be under pressure,
    # but the structure must be consistent.
    if status["under_pressure"]:
        assert len(status.get("message", "")) > 0, (
            "Warning message should be present when under pressure"
        )


# ===========================================================================
# 7. Diagnostics — Bottleneck Identification
# ===========================================================================

def test_diagnostics_bottleneck_identification():
    """Diagnostics identifies the slowest pipeline stage."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    stage_timings = {
        "stt": 500,
        "stage1_punctuation": 50,
        "stage2_grammar": 2000,
        "stage3_formatting": 800,
        "stage4_cleanup": 5,
    }
    bottleneck = d.identify_bottleneck(stage_timings)
    assert isinstance(bottleneck, dict)
    assert "slowest_stage" in bottleneck
    assert bottleneck["slowest_stage"] == "stage2_grammar"
    assert "slowest_ms" in bottleneck
    assert bottleneck["slowest_ms"] == 2000
    assert "percentage_of_total" in bottleneck


def test_diagnostics_bottleneck_empty():
    """Bottleneck identification handles empty stage timings."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    result = d.identify_bottleneck({})
    assert result["slowest_stage"] is None
    assert result["slowest_ms"] == 0


def test_diagnostics_bottleneck_single_stage():
    """Bottleneck with a single stage returns that stage."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    result = d.identify_bottleneck({"only_stage": 100})
    assert result["slowest_stage"] == "only_stage"
    assert result["percentage_of_total"] == 100.0


# ===========================================================================
# 8. Diagnostics — Pipeline Integration Analysis
# ===========================================================================

def test_diagnostics_full_pipeline_analysis():
    """Diagnostics analyzes a full pipeline run end-to-end."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    stage_timings = {
        "stt": 450,
        "stt_to_stage1_handoff": 2,
        "stage1_punctuation": 55,
        "stage2_grammar": 1300,
        "stage3_formatting": 900,
        "stage4_cleanup": 8,
    }
    analysis = d.analyze_pipeline_run(
        audio_duration_sec=4.5,
        stage_breakdown_ms=stage_timings,
        model_name="small.en",
        device="cpu",
        compute_type="int8",
    )
    assert isinstance(analysis, dict)
    assert "rtf_report" in analysis
    assert "memory_status" in analysis
    assert "bottleneck" in analysis
    assert "recommendations" in analysis
    assert len(analysis["recommendations"]) > 0, (
        "Should provide at least one recommendation"
    )


def test_diagnostics_recommendations():
    """Recommendations are actionable and specific to the bottleneck."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()

    # Slow STT → should recommend smaller model
    r = d.analyze_pipeline_run(
        5.0, {"stt": 6000, "stage1_punctuation": 50, "stage4_cleanup": 5},
        model_name="large-v3", device="cpu", compute_type="int8"
    )
    recs = " ".join(r["recommendations"]).lower()
    assert any(w in recs for w in ["model", "smaller", "stt"]), (
        "Should recommend model change when STT is bottleneck"
    )

    # Slow grammar stage → should recommend skipping or model switch
    r2 = d.analyze_pipeline_run(
        3.0, {"stt": 500, "stage1_punctuation": 40, "stage2_grammar": 4000, "stage4_cleanup": 5},
        model_name="small.en", device="cpu", compute_type="int8"
    )
    recs2 = " ".join(r2["recommendations"]).lower()
    assert any(w in recs2 for w in ["grammar", "stage 2", "skip"]), (
        "Should recommend skipping Stage 2 when it's the bottleneck"
    )


# ===========================================================================
# 9. Profiler Integration with Pipeline
# ===========================================================================

def test_pipeline_profiling_hooks():
    """Pipeline orchestrator supports profiling hooks."""
    skip_if(
        not _pipeline_available(),
        "pipeline package not available"
    )
    from perf.profiler import InferenceProfiler
    from pipeline import PipelineOrchestrator

    profiler = InferenceProfiler()
    orch = PipelineOrchestrator()

    # Run the pipeline with profiling hooks injected.
    result = orch.process(
        "hello world this is a test",
        lane="text",
        profiler=profiler,
    )
    assert isinstance(result, str)
    assert len(result) > 0

    # Profiler records stages that actually ran. In the authoritative offline
    # suite the optional punctuation model is deliberately unavailable, so a
    # Stage 1 timing would be false evidence; degradation must be reported instead.
    stage_names = [s.stage_name for s in profiler.stage_timings]
    stage1_ran = (orch._s1_available and orch.stage1 is not None
                  and orch.stage1.available)
    if stage1_ran:
        assert any("stage1" in name for name in stage_names), (
            "An available Stage 1 should be profiled"
        )
    else:
        assert not any("stage1" in name for name in stage_names)
        assert orch.last_degradation is not None
        assert any(item[0] == 1
                   for item in orch.last_degradation["events"])
    assert any("stage4" in name for name in stage_names) or any(
        "cleanup" in name for name in stage_names
    ), "Stage 4 should be profiled"


def test_pipeline_stt_to_stage1_handoff_profiled():
    """STT-to-Stage1 handoff timing is recorded by profiling hooks (VAL-CROSS-004)."""
    skip_if(
        not _pipeline_available(),
        "pipeline package not available"
    )
    from perf.profiler import InferenceProfiler
    from pipeline import PipelineOrchestrator

    profiler = InferenceProfiler()
    orch = PipelineOrchestrator()

    # Simulate what local_engine.run_local_pipeline() does:
    #   1. STT produces raw text
    #   2. handoff: raw text is given to pipeline
    #   3. pipeline runs stages
    raw_text = "hello world this is a test"

    # STT (simulated)
    with profiler.stage("stt_transcription"):
        pass  # STT would complete here

    # Handoff: the gap between STT completion and Stage 1 start.
    # In production, local_engine.run_local_pipeline() is called at this point.
    with profiler.stage("stt_to_stage1_handoff"):
        pass  # Marker for the STT→Stage 1 transition

    # Now run the pipeline (Stage 1 and beyond).
    orch_result = orch.process(raw_text, lane="text", profiler=profiler)

    assert isinstance(orch_result, str)
    assert len(orch_result) > 0

    stage_names = [s.stage_name for s in profiler.stage_timings]
    assert "stt_to_stage1_handoff" in stage_names, (
        "STT-to-Stage1 handoff must be profiled (VAL-CROSS-004)"
    )
    # Verify correct ordering: STT → handoff → Stage 1
    stt_idx = stage_names.index("stt_transcription")
    handoff_idx = stage_names.index("stt_to_stage1_handoff")
    stage1_indices = [i for i, n in enumerate(stage_names)
                      if "pipeline_stage1" in n]
    if stage1_indices:
        stage1_idx = stage1_indices[0]
        assert stt_idx < handoff_idx < stage1_idx, (
            "STT → handoff → Stage 1 must be in order (VAL-CROSS-004)"
        )


def test_pipeline_profiling_disabled():
    """When profiler is disabled, pipeline runs without profiling overhead."""
    skip_if(
        not _pipeline_available(),
        "pipeline package not available"
    )
    from perf.profiler import InferenceProfiler
    from pipeline import PipelineOrchestrator

    profiler = InferenceProfiler(enabled=False)
    orch = PipelineOrchestrator()

    result = orch.process(
        "hello world",
        lane="text",
        profiler=profiler,
    )
    assert isinstance(result, str)
    assert len(result) > 0
    # Disabled profiler should not record anything.
    assert len(profiler.stage_timings) == 0


# ===========================================================================
# 10. Edge Cases
# ===========================================================================

def test_profiler_empty_input():
    """Empty input to profile_pipeline returns empty string."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    result, profile = profiler.profile_pipeline("", lane="text")
    assert result == ""
    assert isinstance(profile, dict)


def test_profiler_long_text():
    """Long text profiling works without error."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    long_text = "hello world " * 100
    result, profile = profiler.profile_pipeline(long_text, lane="text")
    assert isinstance(result, str)
    assert len(result) > 0
    assert "total_ms" in profile


def test_diagnostics_no_stages():
    """Diagnostics handles empty/zero stage timings gracefully."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    r = d.monitor_rtf(5.0, 0.0, {})
    assert r["rtf"] == 0.0
    r2 = d.monitor_rtf(5.0, 0.0, {"stage1": 0})
    assert r2["rtf"] == 0.0


def test_diagnostics_warns_on_high_rtf():
    """High RTF values trigger warnings."""
    from perf.diagnostics import Diagnostics
    d = Diagnostics()
    r = d.monitor_rtf(3.0, 6000.0, {"stt": 4000, "stage1": 2000})
    assert r["warning"]
    assert r["verdict"] in ("slow", "very_slow")


def test_profiler_nested_stages_work():
    """Nested stage contexts (sub-stages) are recorded correctly."""
    from perf.profiler import InferenceProfiler
    profiler = InferenceProfiler()
    with profiler.stage("outer"):
        time.sleep(0.01)
        with profiler.stage("inner"):
            time.sleep(0.005)
    # Context managers append on exit, so the inner (exiting first) is
    # at index 0 and the outer (exiting last) is at index 1.
    assert len(profiler.stage_timings) == 2
    names = [s.stage_name for s in profiler.stage_timings]
    assert names[0] == "inner", f"Expected inner first, got {names}"
    assert names[1] == "outer", f"Expected outer second, got {names}"
    # Outer should take longer than inner (it wraps it).
    inner_ms = profiler.stage_timings[0].elapsed_ms
    outer_ms = profiler.stage_timings[1].elapsed_ms
    assert outer_ms >= inner_ms, (
        f"Outer ({outer_ms:.3f}ms) should take >= inner ({inner_ms:.3f}ms)"
    )


# ===========================================================================
# 11. Optimizer — ThreadTuner
# ===========================================================================

def test_thread_tuner_instantiation():
    """ThreadTuner can be instantiated with defaults."""
    from perf.optimizer import ThreadTuner
    t = ThreadTuner()
    assert t.cpu_count >= 1
    assert t.os_reserved >= 0


def test_thread_tuner_stt_threads():
    """STT threads = max(floor, cpu - reserved)."""
    from perf.optimizer import ThreadTuner
    t = ThreadTuner(cpu_count=8, os_reserved=2)
    assert t.stt_threads() >= 4, "STT floor is 4"
    assert t.stt_threads() == 6, f"Expected 6, got {t.stt_threads()}"
    # On a 4-core system, floor applies.
    t2 = ThreadTuner(cpu_count=4, os_reserved=2)
    assert t2.stt_threads() == 4, f"Expected 4 (floor), got {t2.stt_threads()}"


def test_recommended_stt_threads_prefers_physical_cores():
    """Interactive STT must not blindly consume every SMT thread."""
    from perf.optimizer import recommended_stt_threads

    assert recommended_stt_threads(
        logical_count=16, physical_count=8) == 8
    assert recommended_stt_threads(
        logical_count=4, physical_count=2) == 4
    assert recommended_stt_threads(
        logical_count=2, physical_count=1) == 2


def test_thread_tuner_llm_threads():
    """LLM threads = cpu - reserved."""
    from perf.optimizer import ThreadTuner
    t = ThreadTuner(cpu_count=8, os_reserved=2)
    assert t.llm_threads() == 6
    t2 = ThreadTuner(cpu_count=4, os_reserved=2)
    assert t2.llm_threads() == 2


def test_thread_tuner_minimum():
    """Thread counts never go below 1."""
    from perf.optimizer import ThreadTuner
    t = ThreadTuner(cpu_count=1, os_reserved=2)
    assert t.stt_threads() >= 1
    assert t.llm_threads() >= 1


def test_thread_tuner_recommend():
    """Recommend returns a complete dict."""
    from perf.optimizer import ThreadTuner
    t = ThreadTuner(cpu_count=8, os_reserved=2)
    r = t.recommend()
    assert "cpu_count" in r
    assert "stt_threads" in r
    assert "llm_threads" in r
    assert r["stt_threads"] >= 4
    assert r["llm_threads"] >= 1


# ===========================================================================
# 12. Optimizer — QuantizationSelector
# ===========================================================================

def test_quantization_selector_instantiation():
    """QuantizationSelector can be instantiated."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector()
    assert q.tier in ("weak", "mid", "powerful")


def test_quantization_selector_basic():
    """Quantization returns correct values per tier."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector(tier="weak")
    assert q.recommended() == "none"
    q.tier = "mid"
    assert q.recommended() == "Q4_K_M"
    q.tier = "powerful"
    assert q.recommended() == "Q8_0"


def test_quantization_selector_stage_overrides():
    """Stage-specific quantizations are returned correctly."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector(tier="weak")
    assert q.recommended(stage="stage2_grammar") == "none"
    assert q.recommended(stage="stage3_formatting") == "none"
    q.tier = "mid"
    assert q.recommended(stage="stage2_grammar") == "Q4_K_M"
    q.tier = "powerful"
    assert q.recommended(stage="stage2_grammar") == "Q8_0"
    assert q.recommended(stage="stage3_formatting") == "Q8_0"


def test_quantization_should_download():
    """Weak tier should not download LLM models."""
    from perf.optimizer import QuantizationSelector
    assert not QuantizationSelector(tier="weak").should_download_llm()
    assert QuantizationSelector(tier="mid").should_download_llm()
    assert QuantizationSelector(tier="powerful").should_download_llm()


def test_quantization_is_upgrade():
    """Quant upgrade detection works."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector(tier="mid")
    assert q.is_quant_upgrade("Q4_K_M", "Q8_0")
    assert not q.is_quant_upgrade("Q8_0", "Q4_K_M")
    assert not q.is_quant_upgrade("Q4_K_M", "Q4_K_M")
    assert q.is_quant_upgrade("none", "Q4_K_M")
    assert not q.is_quant_upgrade("Q4_K_M", "none")


def test_quantization_recommend_all():
    """Recommend_all returns a comprehensive dict."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector(tier="mid")
    r = q.recommend_all()
    assert r["tier"] == "mid"
    assert r["default"] == "Q4_K_M"
    assert r["stage2_grammar"] == "Q4_K_M"
    assert r["stage3_formatting"] == "Q4_K_M"
    assert r["stage1_punctuation"] == "none"
    assert r["stage4_cleanup"] == "none"
    assert r["should_download_llm"] is True


def test_quantization_invalid_tier_fallback():
    """Invalid tier falls back to mid."""
    from perf.optimizer import QuantizationSelector
    q = QuantizationSelector(tier="mid")
    assert q.recommended("invalid_tier") == "Q4_K_M"


# ===========================================================================
# 13. Optimizer — LazyLoadScheduler
# ===========================================================================

def test_lazy_load_scheduler_instantiation():
    """LazyLoadScheduler can be instantiated."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler()
    assert l._tier in ("weak", "mid", "powerful")


def test_lazy_load_schedule_weak():
    """Weak tier schedule has LLM stages disabled."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="weak")
    schedule = l.schedule()
    # Find the grammar and formatting entries.
    grammar = next(s for s in schedule if s["stage"] == "stage2_grammar")
    formatting = next(s for s in schedule if s["stage"] == "stage3_formatting")
    assert grammar["load_trigger"] == "never"
    assert formatting["load_trigger"] == "never"
    assert not grammar["enabled"]
    assert not formatting["enabled"]


def test_lazy_load_schedule_mid():
    """Mid tier schedule has LLM stages enabled on first_llm trigger."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="mid")
    schedule = l.schedule()
    grammar = next(s for s in schedule if s["stage"] == "stage2_grammar")
    formatting = next(s for s in schedule if s["stage"] == "stage3_formatting")
    assert grammar["load_trigger"] == "first_llm"
    assert formatting["load_trigger"] == "first_llm"
    assert grammar["enabled"]
    assert formatting["enabled"]


def test_lazy_load_schedule_powerful():
    """Powerful tier schedule enables all stages."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="powerful")
    schedule = l.schedule()
    enabled = [s for s in schedule if s["enabled"]]
    assert len(enabled) == 4  # All four stages


def test_lazy_load_stage_enabled():
    """Stage enablement per tier is correct."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="weak")
    assert l.stage_enabled("stage1_punctuation")
    assert not l.stage_enabled("stage2_grammar")
    assert not l.stage_enabled("stage3_formatting")
    assert l.stage_enabled("stage4_cleanup")

    l2 = LazyLoadScheduler(tier="powerful")
    assert l2.stage_enabled("stage2_grammar")
    assert l2.stage_enabled("stage3_formatting")


def test_lazy_load_should_load_now():
    """Should_load_now respects trigger events."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="mid")
    # Stage 2 should load on first_llm, not on first_stt.
    assert not l.should_load_now("stage2_grammar", "first_stt")
    assert l.should_load_now("stage2_grammar", "first_llm")
    # On weak, it should never load.
    lw = LazyLoadScheduler(tier="weak")
    assert not lw.should_load_now("stage2_grammar", "first_llm")


def test_lazy_load_mark_tracking():
    """Mark loaded/unloaded tracks state correctly."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="mid")
    assert not l.is_anything_loaded
    l.mark_loaded("stage2_grammar")
    assert l.is_anything_loaded
    assert "stage2_grammar" in l.loaded_stages
    l.mark_unloaded("stage2_grammar")
    assert not l.is_anything_loaded
    assert "stage2_grammar" not in l.loaded_stages


def test_lazy_load_on_tier_change():
    """Tier change returns stage enablement diffs."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="weak")
    changes = l.on_tier_change("weak", "powerful")
    assert "stage2_grammar" in changes
    assert changes["stage2_grammar"]["was_enabled"] is False
    assert changes["stage2_grammar"]["now_enabled"] is True
    assert "stage3_formatting" in changes
    assert changes["stage3_formatting"]["was_enabled"] is False
    assert changes["stage3_formatting"]["now_enabled"] is True


def test_lazy_load_reset():
    """Reset clears loaded tracking."""
    from perf.optimizer import LazyLoadScheduler
    l = LazyLoadScheduler(tier="mid")
    l.mark_loaded("stage2_grammar")
    assert l.is_anything_loaded
    l.reset()
    assert not l.is_anything_loaded


# ===========================================================================
# 14. Optimizer — MemoryPressureHandler
# ===========================================================================

def test_memory_pressure_handler_instantiation():
    """MemoryPressureHandler can be instantiated."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    assert m.pressure_threshold_mb > 0
    assert m.memory_budget_mb > 0
    assert m.auto_unload is True


def test_memory_pressure_check():
    """Memory pressure check returns a status dict."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    status = m.check()
    assert isinstance(status, dict)
    assert "under_pressure" in status
    assert "free_memory_mb" in status
    assert "threshold_mb" in status
    assert isinstance(status["under_pressure"], bool)


def test_memory_pressure_is_under_pressure():
    """is_under_pressure returns a boolean."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    result = m.is_under_pressure()
    assert isinstance(result, bool)


def test_memory_pressure_handle_no_manager():
    """Handle memory pressure without a model manager attached."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    result = m.handle_memory_pressure()
    assert isinstance(result, dict)
    assert "was_under_pressure" in result
    assert "unloaded" in result
    # Without a model manager, nothing is unloaded.
    assert result["unloaded"] is False


def test_memory_pressure_should_unload():
    """Should_unload always True under single-resident when model loaded."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    # Without model manager attached, should_unload returns False.
    assert m.should_unload(100) is False


def test_memory_pressure_attach_model_manager():
    """Attaching a model manager works."""
    from perf.optimizer import MemoryPressureHandler
    from models.manager import ModelManager
    m = MemoryPressureHandler()
    mgr = ModelManager()
    m.attach_model_manager(mgr)
    assert m._model_manager is mgr
    # With a model manager, should_unload returns correct value.
    # No model loaded → no need to unload.
    assert m.should_unload(100) is False


def test_memory_pressure_startup_check():
    """Startup check never unloads (no models loaded yet)."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    status = m.startup_check()
    assert isinstance(status, dict)
    assert "under_pressure" in status


def test_memory_pressure_auto_unload_disabled():
    """When auto_unload is False, handle_memory_pressure never unloads."""
    from perf.optimizer import MemoryPressureHandler
    from models.manager import ModelManager
    m = MemoryPressureHandler(auto_unload=False)
    mgr = ModelManager()
    m.attach_model_manager(mgr)
    result = m.handle_memory_pressure()
    assert result["unloaded"] is False


def test_memory_pressure_reset():
    """Reset clears cached state."""
    from perf.optimizer import MemoryPressureHandler
    m = MemoryPressureHandler()
    m._cached_pressure = True
    m.reset()
    # After reset, is_under_pressure should recalculate
    assert m._last_pressure_check == 0.0


# ===========================================================================
# 15. Optimizer — PerfOptimizer (central orchestrator)
# ===========================================================================

def test_perf_optimizer_instantiation():
    """PerfOptimizer can be instantiated with default tier."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    assert opt.tier in ("weak", "mid", "powerful")
    assert opt.threads is not None
    assert opt.quant is not None
    assert opt.lazy is not None
    assert opt.memory is not None


def test_perf_optimizer_explicit_tier():
    """PerfOptimizer accepts an explicit tier."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="weak")
    assert opt.tier == "weak"


def test_perf_optimizer_tier_setter():
    """PerfOptimizer tier setter validates and propagates."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="mid")
    opt.tier = "powerful"
    assert opt.tier == "powerful"
    assert opt._quant.tier == "powerful"
    try:
        opt.tier = "invalid"
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_perf_optimizer_stt_threads():
    """Shortcut for STT thread count."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    assert opt.stt_threads() >= 4


def test_perf_optimizer_llm_threads():
    """Shortcut for LLM thread count."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    assert opt.llm_threads() >= 1


def test_perf_optimizer_recommended_quantization():
    """Shortcut for recommended quantization."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="mid")
    assert opt.recommended_quantization() == "Q4_K_M"
    assert opt.recommended_quantization(stage="stage2_grammar") == "Q4_K_M"


def test_perf_optimizer_lazy_load_schedule():
    """Shortcut for lazy load schedule."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    schedule = opt.lazy_load_schedule()
    assert isinstance(schedule, list)
    assert len(schedule) == 4


def test_perf_optimizer_check_memory_pressure():
    """Shortcut for memory pressure check."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    status = opt.check_memory_pressure()
    assert isinstance(status, dict)
    assert "under_pressure" in status


def test_perf_optimizer_handle_memory_pressure():
    """Shortcut for memory pressure handling."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    result = opt.handle_memory_pressure()
    assert isinstance(result, dict)
    assert "unloaded" in result


def test_perf_optimizer_enforce_single_resident():
    """Single-resident enforcement without a model manager is a no-op."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    result = opt.enforce_single_resident()
    assert result["unloaded"] is False
    assert result["previous_model"] is None


def test_perf_optimizer_enforce_single_resident_with_manager():
    """Single-resident enforcement with an attached manager."""
    from perf.optimizer import PerfOptimizer
    from models.manager import ModelManager
    opt = PerfOptimizer()
    mgr = ModelManager()
    opt.attach_model_manager(mgr)
    result = opt.enforce_single_resident()
    # No model loaded yet, so nothing to unload.
    assert result["unloaded"] is False


def test_perf_optimizer_startup_optimize():
    """Startup optimization returns a complete report."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    report = opt.startup_optimize()
    assert isinstance(report, dict)
    assert "tier" in report
    assert report["tier"] in ("weak", "mid", "powerful")
    assert "threads" in report
    assert "quantization" in report
    assert "deferred" in report
    assert "immediate" in report
    assert "pipeline_stages" in report
    assert "memory_status" in report
    # Thread tuning and quantization are immediate.
    assert "thread_tuning" in report["immediate"]
    assert "quantization_selection" in report["immediate"]
    # Model scanning and loading are deferred.
    assert "scan_models" in report["deferred"]
    assert "load_punctuation_model" in report["deferred"]


def test_perf_optimizer_cascade_tier_change(VAL_CROSS_009=True):
    """Hardware tier change cascades to STT, pipeline, quantization.

    VAL-CROSS-009: Changing hardware_tier from 'weak' to 'powerful' MUST cause:
      (a) STT model change
      (b) pipeline stage enablement changes
      (c) quantization change
    """
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="weak")
    cascade = opt.cascade_tier_change("weak", "powerful")
    assert isinstance(cascade, dict)
    assert cascade["old_tier"] == "weak"
    assert cascade["new_tier"] == "powerful"

    # (a) STT model change.
    assert cascade["stt_model_change"]["changed"] is True
    assert cascade["stt_model_change"]["old"] != cascade["stt_model_change"]["new"]

    # (b) Pipeline stage changes.
    assert "stage2_grammar" in cascade["pipeline_stage_changes"]
    assert cascade["pipeline_stage_changes"]["stage2_grammar"]["now_enabled"] is True
    assert "stage3_formatting" in cascade["pipeline_stage_changes"]
    assert cascade["pipeline_stage_changes"]["stage3_formatting"]["now_enabled"] is True

    # (c) Quantization change.
    assert cascade["quantization_change"]["changed"] is True
    assert cascade["quantization_change"]["is_upgrade"] is True
    assert cascade["quantization_change"]["old"] == "none"
    assert cascade["quantization_change"]["new"] == "Q8_0"

    # Recommendations should be present.
    assert len(cascade["recommendations"]) >= 3


def test_perf_optimizer_cascade_no_change():
    """Tier change to same tier has no changes."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="mid")
    cascade = opt.cascade_tier_change("mid", "mid")
    assert cascade["stt_model_change"]["changed"] is False
    assert cascade["quantization_change"]["changed"] is False
    assert len(cascade["pipeline_stage_changes"]) == 0


def test_perf_optimizer_reevaluate_pipeline_stages():
    """Re-evaluating pipeline stages returns a status dict (VAL-CROSS-005)."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer(hardware_tier="mid")
    eval_result = opt.reevaluate_pipeline_stages()
    assert isinstance(eval_result, dict)
    assert "tier" in eval_result
    assert "stages" in eval_result
    assert "newly_available" in eval_result
    assert "recommendation" in eval_result
    # Stage 4 is always available.
    assert eval_result["stages"]["stage4_cleanup"]["available"] is True


def test_perf_optimizer_reevaluate_with_manager():
    """Re-evaluation with a model manager checks model presence."""
    from perf.optimizer import PerfOptimizer
    from models.manager import ModelManager
    opt = PerfOptimizer(hardware_tier="mid")
    mgr = ModelManager()
    opt.attach_model_manager(mgr)
    eval_result = opt.reevaluate_pipeline_stages()
    assert eval_result["stages"]["stage4_cleanup"]["available"] is True


def test_perf_optimizer_full_recommendation():
    """Full recommendation returns a comprehensive dict."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    rec = opt.full_recommendation()
    assert isinstance(rec, dict)
    for key in ("tier", "threads", "quantization", "lazy_load_schedule",
                "pipeline_stages", "memory_status"):
        assert key in rec, f"Missing key: {key}"


def test_perf_optimizer_attach_all():
    """Attaching model manager, registry, pipeline, settings works."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    # These should not raise even with None or mock objects.
    opt.attach_model_manager(None)
    opt.attach_model_registry(None)
    opt.attach_pipeline(None)
    opt.attach_settings(None)


def test_perf_optimizer_get_optimizer():
    """get_optimizer returns a singleton."""
    from perf.optimizer import get_optimizer
    opt1 = get_optimizer()
    opt2 = get_optimizer()
    assert opt1 is opt2  # Same singleton


def test_perf_optimizer_should_unload():
    """should_unload_before_load shortcut works."""
    from perf.optimizer import PerfOptimizer
    opt = PerfOptimizer()
    result = opt.should_unload_before_load(100)
    assert isinstance(result, bool)


# ===========================================================================
# Helpers
# ===========================================================================

def _pipeline_available():
    try:
        from pipeline import PipelineOrchestrator  # noqa: F401
        return True
    except ImportError:
        return False


# ===========================================================================
# Runner
# ===========================================================================

def run_tests():
    global _passed, _failed, _skipped
    _passed = _failed = _skipped = 0
    tests = sorted(
        (k, v) for k, v in globals().items()
        if k.startswith("test_") and callable(v)
    )
    print(f"Running {len(tests)} perf tests...")
    for name, func in tests:
        _run(name, func)
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    return _failed


if __name__ == "__main__":
    sys.exit(run_tests())
