"""Public command-line contract tests for Mumble's isolated test runner."""

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


RUNNER = Path(__file__).with_name("run_tests.py")
PORT_RUNNERS = [
    Path(__file__).parent / "Ports" / "macOS" / "app" / "run_tests.py",
    Path(__file__).parent / "Ports" / "Linux" / "app" / "run_tests.py",
]


def _run_fixture(tmp_path, source):
    fixture = tmp_path / "test_probe.py"
    fixture.write_text(source, encoding="utf-8")
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--test-dir",
            str(tmp_path),
            "--pattern",
            "probe",
            "--json",
            str(report),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return completed, payload


def test_runner_executes_pytest_module_and_reports_case_count(tmp_path):
    completed, payload = _run_fixture(
        tmp_path,
        "def test_real_case():\n"
        "    assert 6 * 7 == 42\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["module_count"] == 1
    assert payload["case_count"] == 1
    assert payload["results"][0]["style"] == "pytest"
    assert payload["results"][0]["cases"] == 1


def test_runner_counts_pytest_cases_even_with_a_pytest_main_guard(tmp_path):
    completed, payload = _run_fixture(
        tmp_path,
        "import pytest\n"
        "\n"
        "def test_real_case():\n"
        "    assert True\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(pytest.main([__file__]))\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["case_count"] == 1
    assert payload["results"][0]["style"] == "pytest"


def test_runner_preserves_a_custom_procedural_test_harness(tmp_path):
    marker = tmp_path / "custom-harness.ran"
    completed, payload = _run_fixture(
        tmp_path,
        "from pathlib import Path\n"
        "\n"
        "def test_needs_custom_root(root):\n"
        f"    Path({str(marker)!r}).write_text(root, encoding='utf-8')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    test_needs_custom_root('custom setup executed')\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert marker.read_text(encoding="utf-8") == "custom setup executed"
    assert payload["case_count"] == 1
    assert payload["results"][0]["style"] == "procedural"


def test_runner_executes_unittest_module_and_reports_each_case(tmp_path):
    completed, payload = _run_fixture(
        tmp_path,
        "import unittest\n"
        "\n"
        "class ArithmeticTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(2 + 3, 5)\n"
        "\n"
        "    def test_multiply(self):\n"
        "        self.assertEqual(6 * 7, 42)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload["module_count"] == 1
    assert payload["case_count"] == 2
    assert payload["results"][0]["style"] == "unittest"
    assert payload["results"][0]["cases"] == 2


def test_runner_fails_a_module_that_collects_zero_tests(tmp_path):
    completed, payload = _run_fixture(
        tmp_path,
        "VALUE_DEFINED_BUT_NEVER_TESTED = 42\n",
    )

    assert completed.returncode != 0
    assert payload["module_count"] == 1
    assert payload["case_count"] == 0
    assert payload["failed"] == 1
    assert payload["results"][0]["status"] == "failed"


def test_runner_fails_when_no_modules_are_discovered(tmp_path):
    report = tmp_path / "empty.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--test-dir",
            str(tmp_path),
            "--json",
            str(report),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode != 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["module_count"] == 0
    assert payload["case_count"] == 0
    assert payload["failed"] == 1
    assert payload["runner_errors"] == ["No test modules were discovered."]


def test_junit_records_runner_error_when_pytest_produces_no_cases(tmp_path):
    (tmp_path / "test_probe.py").write_text(
        "VALUE_DEFINED_BUT_NEVER_TESTED = 42\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    junit = tmp_path / "report.xml"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--test-dir",
            str(tmp_path),
            "--json",
            str(report),
            "--junit",
            str(junit),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode != 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["case_count"] == 0
    root = ET.parse(junit).getroot()
    assert root.get("tests") == "1"
    assert root.get("errors") == "1"
    assert root.find("./testsuite/testcase/error") is not None


def test_runner_writes_aggregate_junit_evidence(tmp_path):
    (tmp_path / "test_probe.py").write_text(
        "def test_visible_in_junit():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    junit = tmp_path / "report.xml"
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--test-dir",
            str(tmp_path),
            "--pattern",
            "probe",
            "--json",
            str(report),
            "--junit",
            str(junit),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    root = ET.parse(junit).getroot()
    assert root.tag == "testsuites"
    assert root.get("tests") == "1"
    assert root.get("failures") == "0"
    assert root.find("./testsuite/testcase") is not None


def test_runner_executes_procedural_module_as_one_atomic_case(tmp_path):
    marker = tmp_path / "probe.ran"
    completed, payload = _run_fixture(
        tmp_path,
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "print('ALL PASS (1)')\n",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert marker.read_text(encoding="utf-8") == "executed"
    assert payload["case_count"] == 1
    assert payload["results"][0]["style"] == "procedural"


def test_platform_runners_use_the_same_pytest_and_case_count_contract(tmp_path):
    (tmp_path / "test_probe.py").write_text(
        "def test_port_case():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    for runner in PORT_RUNNERS:
        report = tmp_path / f"{runner.parents[1].name}.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--all",
                "--test-dir",
                str(tmp_path),
                "--pattern",
                "probe",
                "--json",
                str(report),
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["module_count"] == 1
        assert payload["case_count"] == 1
        assert payload["results"][0]["style"] == "pytest"


def test_offline_suite_does_not_require_a_cached_whisper_snapshot(tmp_path):
    env = dict(os.environ)
    env.update({
        "MUMBLE_OFFLINE_TESTS": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": str(tmp_path / "empty-huggingface-home"),
        "PYTHONUTF8": "1",
    })
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("test_lightweight.py"))],
        cwd=Path(__file__).parent,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "offline suite does not require a cached model snapshot" in completed.stdout


def test_port_offline_suites_do_not_require_cached_whisper_snapshots(tmp_path):
    env = dict(os.environ)
    env.update({
        "MUMBLE_OFFLINE_TESTS": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": str(tmp_path / "empty-port-huggingface-home"),
        "PYTHONUTF8": "1",
    })
    for port in ("macOS", "Linux"):
        script = Path(__file__).parent / "Ports" / port / "app" / "test_lightweight.py"
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=script.parent,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "offline suite does not require a cached model snapshot" in completed.stdout
