"""Shared execution engine for Mumble's isolated Python test modules."""

import argparse
import ast
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


LIVE_PREFIX = "test_live_"
SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "SUPABASE", "CREDENTIAL")


def discover_tests(here, *, live=False, pattern="", safe_tests=None, run_all=False):
    rows = [os.path.basename(path) for path in glob.glob(os.path.join(here, "test_*.py"))]
    rows = [name for name in rows if name.startswith(LIVE_PREFIX) == live]
    if safe_tests is not None and not run_all:
        rows = [name for name in rows if name in safe_tests]
    if pattern:
        rows = [name for name in rows if pattern.lower() in name.lower()]
    return sorted(rows)


def module_style(path):
    """Classify a module by the public test protocol needed to execute it."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, SyntaxError, UnicodeError):
        return "procedural"

    has_tests = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in tree.body
    )
    has_unittest_cases = any(
        isinstance(node, ast.ClassDef)
        and any(
            (isinstance(base, ast.Attribute) and base.attr == "TestCase")
            or (isinstance(base, ast.Name) and base.id == "TestCase")
            for base in node.bases
        )
        for node in tree.body
    )
    has_top_level_test_call = any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", "").startswith("test_")
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
    script_entries = [
        node for node in tree.body
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
    ]
    entry_uses_pytest = any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "pytest"
        and child.func.attr == "main"
        for entry in script_entries
        for child in ast.walk(entry)
    )
    has_top_level_call = any(
        not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                              ast.AsyncFunctionDef, ast.ClassDef))
        and any(isinstance(child, ast.Call) for child in ast.walk(node))
        for node in tree.body
    )
    if has_unittest_cases:
        return "unittest"
    # A guard which directly delegates to pytest remains a pytest module. Other
    # guards are custom harnesses: they may prepare non-fixture arguments,
    # translate skips, aggregate check() calls, or clean up external resources.
    if (has_tests and (not script_entries or entry_uses_pytest)
            and not has_top_level_test_call and not has_top_level_exit):
        return "pytest"
    if (not has_tests and not script_entries and not has_top_level_test_call
            and not has_top_level_exit and not has_top_level_call):
        return "pytest"
    return "procedural"


def _pytest_evidence(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {"cases": 0, "failures": 0, "errors": 0, "skipped": 0,
                "case_results": []}
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    evidence = {
        "cases": sum(int(suite.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.get("skipped", 0)) for suite in suites),
        "case_results": [],
    }
    for case in root.findall(".//testcase"):
        status = "passed"
        message = ""
        for child_status in ("failure", "error", "skipped"):
            child = case.find(child_status)
            if child is not None:
                status = child_status
                message = child.get("message", "") or (child.text or "")[:500]
                break
        evidence["case_results"].append({
            "classname": case.get("classname", ""),
            "name": case.get("name", "unnamed"),
            "seconds": float(case.get("time", 0) or 0),
            "status": status,
            "message": message,
        })
    return evidence


def _environment(root, test, *, live):
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "MUMBLE_OFFLINE_TESTS": "0" if live else "1",
        "HF_HUB_OFFLINE": "0" if live else "1",
        "MUMBLE_TEST_DATA_DIR": os.path.join(root, test[:-3]),
    })
    if not live:
        for key in list(env):
            if any(marker in key.upper() for marker in SECRET_MARKERS):
                env.pop(key, None)
    return env


def run_suite(here, tests, *, live=False, timeout=180):
    if not tests:
        return {
            "mode": "live" if live else "offline",
            "total": 0,
            "passed": 0,
            "failed": 1,
            "module_count": 0,
            "case_count": 0,
            "seconds": 0.0,
            "runner_errors": ["No test modules were discovered."],
            "results": [],
        }

    temp_root = tempfile.TemporaryDirectory(
        prefix="mumble_tests_", ignore_cleanup_errors=True)
    results = []
    started = time.time()
    try:
        for test in tests:
            path = os.path.join(here, test)
            style = module_style(path)
            module_xml = os.path.join(temp_root.name, f"{test[:-3]}.xml")
            command = [sys.executable, path]
            if style in {"pytest", "unittest"}:
                command = [
                    sys.executable, "-m", "pytest", "-q", path,
                    f"--junitxml={module_xml}", "-p", "no:cacheprovider",
                ]
            print(f"\n{'=' * 64}\n{test} [{style}]\n{'=' * 64}", flush=True)
            t0 = time.time()
            timed_out = False
            try:
                rc = subprocess.run(
                    command,
                    cwd=here,
                    env=_environment(temp_root.name, test, live=live),
                    timeout=max(1, timeout),
                ).returncode
            except subprocess.TimeoutExpired:
                rc, timed_out = 124, True
                print(f"TIMEOUT after {timeout}s: {test}")
            counts = (
                _pytest_evidence(module_xml)
                if style in {"pytest", "unittest"}
                else {
                    "cases": 1,
                    "junit_cases": 1,
                    "failures": int(rc != 0),
                    "errors": 0,
                    "skipped": 0,
                    "case_results": [{
                        "classname": test[:-3],
                        "name": "procedural script",
                        "seconds": round(time.time() - t0, 2),
                        "status": "passed" if rc == 0 else "failure",
                        "message": "" if rc == 0 else f"process exited {rc}",
                    }],
                }
            )
            if style in {"pytest", "unittest"}:
                counts["junit_cases"] = counts["cases"]
                if rc != 0 and not counts["failures"] and not counts["errors"]:
                    counts["errors"] = 1
                    counts["junit_cases"] += 1
                    counts["case_results"].append({
                        "classname": test[:-3],
                        "name": "runner evidence",
                        "seconds": round(time.time() - t0, 2),
                        "status": "error",
                        "message": (
                            f"test process exited {rc} without equivalent "
                            "failure evidence"
                        ),
                    })
            results.append({
                "test": test,
                "style": style,
                "status": "timeout" if timed_out else ("passed" if rc == 0 else "failed"),
                "returncode": rc,
                "seconds": round(time.time() - t0, 2),
                **counts,
            })
    finally:
        temp_root.cleanup()

    failed = [row for row in results if row["returncode"]]
    return {
        "mode": "live" if live else "offline",
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "module_count": len(results),
        "case_count": sum(row["cases"] for row in results),
        "seconds": round(time.time() - started, 2),
        "runner_errors": [],
        "results": results,
    }


def write_junit(report, path):
    runner_errors = report.get("runner_errors", [])
    suites = ET.Element("testsuites", {
        "name": "Mumble isolated tests",
        "tests": str(
            sum(row.get("junit_cases", row["cases"]) for row in report["results"])
            + len(runner_errors)
        ),
        "failures": str(sum(row["failures"] for row in report["results"])),
        "errors": str(
            sum(row["errors"] for row in report["results"]) + len(runner_errors)
        ),
        "skipped": str(sum(row["skipped"] for row in report["results"])),
        "time": str(report["seconds"]),
    })
    for row in report["results"]:
        suite = ET.SubElement(suites, "testsuite", {
            "name": row["test"],
            "tests": str(row.get("junit_cases", row["cases"])),
            "failures": str(row["failures"]),
            "errors": str(row["errors"]),
            "skipped": str(row["skipped"]),
            "time": str(row["seconds"]),
        })
        for case in row["case_results"]:
            node = ET.SubElement(suite, "testcase", {
                "classname": case["classname"] or row["test"][:-3],
                "name": case["name"],
                "time": str(case["seconds"]),
            })
            if case["status"] != "passed":
                child = ET.SubElement(node, case["status"])
                child.set("message", case["message"])
    if runner_errors:
        suite = ET.SubElement(suites, "testsuite", {
            "name": "runner discovery",
            "tests": str(len(runner_errors)),
            "failures": "0",
            "errors": str(len(runner_errors)),
            "skipped": "0",
            "time": "0",
        })
        for message in runner_errors:
            node = ET.SubElement(suite, "testcase", {
                "classname": "runner",
                "name": "test discovery",
                "time": "0",
            })
            child = ET.SubElement(node, "error")
            child.set("message", message)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    ET.ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)


def main(*, here, safe_tests=None, allow_live=True, argv=None):
    parser = argparse.ArgumentParser()
    if allow_live:
        parser.add_argument("--live", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--pattern", default="")
    parser.add_argument("--json", dest="json_path", default="")
    parser.add_argument("--junit", dest="junit_path", default="")
    parser.add_argument("--test-dir", default="")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    live = bool(getattr(args, "live", False))
    if live and os.environ.get("MUMBLE_RUN_LIVE_TESTS") != "1":
        parser.error("live tests require MUMBLE_RUN_LIVE_TESTS=1")
    test_dir = os.path.abspath(args.test_dir or here)
    tests = discover_tests(
        test_dir,
        live=live,
        pattern=args.pattern,
        safe_tests=safe_tests,
        run_all=args.all,
    )
    if args.list:
        print("\n".join(tests))
        return 0
    report = run_suite(test_dir, tests, live=live, timeout=args.timeout)
    print("\n" + json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    if args.json_path:
        parent = os.path.dirname(os.path.abspath(args.json_path))
        os.makedirs(parent, exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    if args.junit_path:
        write_junit(report, args.junit_path)
    return 1 if report["failed"] else 0
