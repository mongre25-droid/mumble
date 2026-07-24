#!/usr/bin/env python3
"""Authoritative isolated offline test runner for the macOS/Linux port trees."""

import argparse
import ast
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
    "test_bindings.py", "test_formatting.py", "test_mode_select.py",
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
    "test_stt_providers.py", "test_tts_providers.py",
    "test_mac_platform.py", "test_linux_platform.py",
    "test_linux_parity_regressions.py", "test_linux_runtime_regressions.py",
    "test_linux_security_regressions.py", "test_prompt_template_registry.py",
    "test_system_search.py",
    "test_recording_limits.py",
}


def _tests(here, run_all=False, pattern=""):
    rows = [os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py"))]
    rows = [p for p in rows if not p.startswith(LIVE_PREFIX)]
    if not run_all:
        rows = [p for p in rows if p in SAFE_TESTS]
    if pattern:
        rows = [p for p in rows if pattern.lower() in p.lower()]
    return sorted(rows)


def _uses_pytest(path):
    """Return True for test modules that define tests but no script entry point.

    The port carries two deliberate test styles: self-running scripts and regular
    pytest modules.  Launching a pytest module with ``python test_x.py`` merely
    defines its functions and exits successfully, which used to report dozens of
    tests as passed without executing any of them.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, SyntaxError, UnicodeError):
        return False

    has_tests = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    has_script_entry = any(
        isinstance(node, ast.If)
        and any(
            isinstance(name, ast.Name) and name.id == "__name__"
            for name in ast.walk(node.test)
        )
        and any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in ast.walk(node.test)
        )
        for node in tree.body
    )
    # Some legacy scripts call their test_* functions directly at module scope
    # (and may finish with sys.exit) without an __main__ guard. Pytest importing
    # those files executes the whole script during collection, then treats the
    # deliberate exit as an INTERNALERROR. Keep those on the script path.
    has_top_level_test_call = any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id.startswith("test_")
        for node in tree.body
    )
    has_top_level_exit = any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "sys"
        and node.value.func.attr == "exit"
        for node in tree.body
    )
    return (has_tests and not has_script_entry
            and not has_top_level_test_call and not has_top_level_exit)


def _test_command(here, test):
    path = os.path.join(here, test)
    if _uses_pytest(path):
        return [sys.executable, "-m", "pytest", "-q", path]
    return [sys.executable, path]


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
                _test_command(here, test),
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
        report_dir = os.path.dirname(os.path.abspath(args.json_path))
        os.makedirs(report_dir, exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
