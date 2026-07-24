#!/usr/bin/env python3
"""Authoritative isolated offline test runner for the macOS/Linux port trees."""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

LIVE_PREFIX = "test_live_"
SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "SUPABASE", "CREDENTIAL")
SAFE_TESTS = {
    "test_bindings.py", "test_formatting.py",
    "test_big_shift.py", "test_foreign_boost.py", "test_local_engine.py",
    "test_model_free.py", "test_presets.py", "test_favorites.py",
    "test_webui_api.py", "test_cmd_auth.py", "test_settings_merge.py",
    "test_context_store.py", "test_meeting.py", "test_stats.py", "test_tips.py",
    "test_local_wiring.py", "test_meeting_controller.py",
    "test_core_engine_audit.py", "test_ui_bug_regressions.py",
    "test_service_platform_regressions.py",
    "test_core_port_regressions.py", "test_service_port_regressions.py",
    "test_ui_port_regressions.py",
    "test_bug_audit_regressions.py", "test_downloader.py",
    "test_model_backend.py", "test_providers.py", "test_settings_recovery.py",
    "test_perf.py", "test_pipeline_stage2_stage3.py",
    "test_recording_limits.py",
    "test_recording_gate.py", "test_stream_seam.py",
    "test_stt_providers.py", "test_tts_providers.py",
    "test_mac_platform.py", "test_linux_platform.py",
    "test_mac_ui_parity_remediation.py",
    "test_macos_audio.py",
    "test_mac_audit_regressions.py",
    "test_linux_parity_regressions.py",
}


def _tests(here, run_all=False, pattern=""):
    rows = [os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py"))]
    rows = [p for p in rows if not p.startswith(LIVE_PREFIX)]
    if not run_all:
        rows = [p for p in rows if p in SAFE_TESTS]
    if pattern:
        rows = [p for p in rows if pattern.lower() in p.lower()]
    return sorted(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true",
                        help="include slow, hardware, and integration scripts")
    parser.add_argument("--pattern", default="")
    parser.add_argument("--json", dest="json_path", default="")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    tests = _tests(here, run_all=args.all, pattern=args.pattern)
    if args.list:
        print("\n".join(tests))
        return 0

    temp_root = tempfile.TemporaryDirectory(
        prefix="mumble_port_tests_", ignore_cleanup_errors=True)
    results = []
    started = time.time()
    for test in tests:
        env = dict(os.environ)
        env.update({
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "MUMBLE_OFFLINE_TESTS": "1", "HF_HUB_OFFLINE": "1",
            "MUMBLE_TEST_DATA_DIR": os.path.join(temp_root.name, test[:-3]),
        })
        for key in list(env):
            if any(marker in key.upper() for marker in SECRET_MARKERS):
                env.pop(key, None)
        print(f"\n{'=' * 64}\n{test}\n{'=' * 64}", flush=True)
        t0 = time.time()
        timed_out = False
        try:
            rc = subprocess.run(
                [sys.executable, os.path.join(here, test)],
                cwd=here, env=env, timeout=max(1, args.timeout),
            ).returncode
        except subprocess.TimeoutExpired:
            rc, timed_out = 124, True
            print(f"TIMEOUT after {args.timeout}s: {test}")
        results.append({
            "test": test,
            "status": "timeout" if timed_out else ("passed" if rc == 0 else "failed"),
            "returncode": rc,
            "seconds": round(time.time() - t0, 2),
        })

    temp_root.cleanup()
    failed = [row for row in results if row["returncode"]]
    report = {
        "mode": "offline-all" if args.all else "offline-safe",
        "total": len(results), "passed": len(results) - len(failed),
        "failed": len(failed), "seconds": round(time.time() - started, 2),
        "results": results,
    }
    print("\n" + json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    if args.json_path:
        parent = os.path.dirname(os.path.abspath(args.json_path))
        os.makedirs(parent, exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
