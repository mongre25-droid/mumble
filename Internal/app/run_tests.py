#!/usr/bin/env python3
"""Authoritative subprocess runner for Mumble's procedural test scripts.

Offline mode is the default. It isolates app data for every script and removes
credential environment variables. Live tests require both ``--live`` and
``MUMBLE_RUN_LIVE_TESTS=1``.
"""

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


def _tests(here, live=False, pattern=""):
    rows = [os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py"))]
    rows = [p for p in rows if p.startswith(LIVE_PREFIX) == live]
    if pattern:
        rows = [p for p in rows if pattern.lower() in p.lower()]
    return sorted(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--pattern", default="")
    parser.add_argument("--json", dest="json_path", default="")
    parser.add_argument("--timeout", type=int, default=180,
                        help="per-test timeout in seconds (default: 180)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.live and os.environ.get("MUMBLE_RUN_LIVE_TESTS") != "1":
        parser.error("live tests require MUMBLE_RUN_LIVE_TESTS=1")

    here = os.path.dirname(os.path.abspath(__file__))
    tests = _tests(here, live=args.live, pattern=args.pattern)
    if args.list:
        print("\n".join(tests))
        return 0

    # Own the isolated app-data tree for exactly this run. The previous mkdtemp
    # path was never removed, so repeated audits accumulated full per-test data
    # directories in the user's temp folder.
    temp_root = tempfile.TemporaryDirectory(
        prefix="mumble_tests_", ignore_cleanup_errors=True)
    root = temp_root.name
    results = []
    started = time.time()
    for test in tests:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MUMBLE_OFFLINE_TESTS"] = "0" if args.live else "1"
        env["HF_HUB_OFFLINE"] = "0" if args.live else "1"
        env["MUMBLE_TEST_DATA_DIR"] = os.path.join(root, test[:-3])
        if not args.live:
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
            rc = 124
            timed_out = True
            print(f"TIMEOUT after {args.timeout}s: {test}")
        results.append({"test": test,
                        "status": "timeout" if timed_out else
                                  ("passed" if rc == 0 else "failed"),
                        "returncode": rc, "seconds": round(time.time() - t0, 2)})

    failed = [row for row in results if row["returncode"]]
    temp_root.cleanup()
    report = {"mode": "live" if args.live else "offline", "total": len(results),
              "passed": len(results) - len(failed), "failed": len(failed),
              "seconds": round(time.time() - started, 2), "results": results}
    print("\n" + json.dumps({k: v for k, v in report.items() if k != "results"},
                             indent=2))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
