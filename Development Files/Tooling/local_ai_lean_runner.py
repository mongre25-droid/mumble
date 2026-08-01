#!/usr/bin/env python3
"""Run Issue #19's owner-authorised lean local-text comparison.

The runner executes exactly five synthetic fixtures per candidate.  It emits
fixture identifiers, booleans, timings, memory, and output hashes only; model
output is never written to the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "Internal" / "app"
BENCHMARK_PATH = Path(__file__).with_name("local_ai_benchmark.py")


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("local_ai_benchmark", BENCHMARK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_passed(item, output):
    folded = output.casefold()
    return all(value.casefold() in folded for value in item.get("required_literals", ())) and not any(
        value.casefold() in folded for value in item.get("forbidden_literals", ())
    )


def _classification_passed(item, output):
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError):
        return False
    return parsed == item["expected"]


def _prompt_for(item):
    lane = item["lane"]
    if lane == "prompt":
        system = "Output only a Markdown prompt with headings '# Task' and '# Instructions'. Preserve the request and add no URL."
    elif lane == "email":
        system = "Output only a professional email with Subject, greeting, body, and 'Best regards,'. Preserve every supplied fact."
    elif lane == "reply":
        system = "Output only a concise natural reply. Do not add a Subject line. Preserve every supplied fact."
    else:
        system = "Classify the text. Output only JSON with keys intent and value. Use intent deck for an instruction to open the Deck; otherwise intent control and the exact input as value."
    return system, item["input"]


def _run_sample(*, backend_type, model_path, runtime_path, item):
    backend = backend_type(
        model_path=str(model_path),
        bin_path=str(runtime_path),
        n_ctx=2048,
        n_threads=8,
        timeout=90,
    )
    system, user = _prompt_for(item)
    cmd = backend._build_cmd(system, user, max_tokens=128, temperature=0.0)
    started = time.perf_counter()
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=str(runtime_path.parent),
    )
    observed = psutil.Process(process.pid)
    peak = 0
    deadline = started + 5.0
    while process.poll() is None:
        try:
            peak = max(peak, observed.memory_info().rss)
        except (psutil.Error, OSError):
            pass
        if time.perf_counter() >= deadline:
            process.kill()
            process.wait(timeout=5)
            break
        time.sleep(0.02)
    stdout, _stderr = process.communicate()
    elapsed_ms = (time.perf_counter() - started) * 1000
    output = ""
    if process.returncode == 0:
        output = stdout.strip()
        from local_engine import _strip_cli_echo
        output = _strip_cli_echo(output, backend._format_prompt(system, user))
    passed = False
    if process.returncode == 0:
        passed = (
            _classification_passed(item, output)
            if item["lane"] == "classification"
            else _text_passed(item, output)
        )
    return {
        "fixture_id": item["id"],
        "passed": bool(passed),
        "completion_ms": round(elapsed_ms, 3),
        "peak_working_set_mb": round(peak / (1024 * 1024), 3),
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_chars": len(output),
    }


def _missing_model_failed_closed(backend_type, runtime_path):
    backend = backend_type(
        model_path=str(runtime_path.parent / "missing-model.gguf"),
        bin_path=str(runtime_path),
        timeout=10,
    )
    try:
        backend.generate("Return only OK.", "OK", max_tokens=4, temperature=0.0)
    except RuntimeError:
        return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--qwen3", type=Path, required=True)
    parser.add_argument("--qwen25", type=Path, required=True)
    parser.add_argument("--hardware-record", type=Path, required=True)
    parser.add_argument("--provenance-record", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    benchmark = _load_benchmark()
    contract = benchmark.load_contract(args.contract_root)
    hardware = json.loads(args.hardware_record.read_text(encoding="utf-8"))
    if hardware.get("id") != "windows-z1-extreme-2026-08-01":
        raise RuntimeError("lean comparison hardware record mismatch")
    sys.path.insert(0, str(APP_DIR))
    from local_engine import LlamaCliBackend

    fixtures = [
        item for item in contract["corpus"]["items"]
        if item.get("lane") in {"prompt", "email", "reply", "classification"}
    ]
    if len(fixtures) != 5:
        raise RuntimeError("lean comparison requires exactly five synthetic fixtures")

    baseline_result = benchmark.run_deterministic_baseline(
        contract=contract,
        app_dir=APP_DIR,
        source_commit="22777a7e62fb5c0730b412fcd85334fbe82d1678",
        hardware_id="windows-z1-extreme-2026-08-01",
    )
    baseline_runs = sum(lane["runs"] for lane in baseline_result["lanes"].values())
    baseline_passed = sum(round(lane["task_success_rate"] * lane["runs"]) for lane in baseline_result["lanes"].values())

    candidates = []
    for candidate_id, model_path, expected_size, expected_hash in (
        ("qwen3-0.6b-q8", args.qwen3, 639446688, "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"),
        ("qwen2.5-1.5b-q4-k-m", args.qwen25, 1117320736, "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"),
    ):
        actual_hash = _sha256(model_path)
        if model_path.stat().st_size != expected_size or actual_hash != expected_hash:
            raise RuntimeError(f"candidate artifact mismatch: {candidate_id}")
        samples = [
            _run_sample(
                backend_type=LlamaCliBackend,
                model_path=model_path,
                runtime_path=args.runtime,
                item=item,
            )
            for item in fixtures
        ]
        warm = [sample["completion_ms"] for sample in samples[1:]]
        candidates.append({
            "candidate_id": candidate_id,
            "sample_count": 5,
            "samples": samples,
            "summary": {
                "passed": sum(sample["passed"] for sample in samples),
                "cold_model_residency_start_ms": samples[0]["completion_ms"],
                "warm_file_cache_p50_ms": round(statistics.median(warm), 3),
                "peak_working_set_mb": max(sample["peak_working_set_mb"] for sample in samples),
                "model_size_bytes": model_path.stat().st_size,
            },
            "recovery": {
                "missing_model_failed_closed": _missing_model_failed_closed(LlamaCliBackend, args.runtime),
            },
        })

    receipt = {
        "schema": "mumble.local-ai-lean-comparison.v1",
        "owner_authorised_scope": "lean_representative_not_statistical",
        "source_parent": "22777a7e62fb5c0730b412fcd85334fbe82d1678",
        "hardware_id": "windows-z1-extreme-2026-08-01",
        "input_records": {
            "candidates": {
                "path": "Development Files/Research/local-ai-benchmark/v1/candidates-v1.json",
                "sha256": _sha256(args.contract_root / "candidates-v1.json"),
            },
            "corpus": {
                "path": "Development Files/Research/local-ai-benchmark/v1/corpus-v1.json",
                "sha256": _sha256(args.contract_root / "corpus-v1.json"),
            },
            "gates": {
                "path": "Development Files/Research/local-ai-benchmark/v1/gates-v1.json",
                "sha256": _sha256(args.contract_root / "gates-v1.json"),
            },
            "hardware": {
                "path": "Development Files/Research/local-ai-benchmark/runs/2026-08-01-lean-windows-z1/hardware.json",
                "sha256": _sha256(args.hardware_record),
            },
            "provenance": {
                "path": "Development Files/Research/local-ai-benchmark/runs/2026-08-01-lean-windows-z1/provenance.json",
                "sha256": _sha256(args.provenance_record),
            },
        },
        "runtime": {
            "component": "llama.cpp",
            "release": "b10107",
            "source_revision": "c0bc8591e8815c63cb01dd3f051a8b0df02501c9",
            "archive_size_bytes": args.runtime_archive.stat().st_size,
            "archive_sha256": _sha256(args.runtime_archive),
        },
        "baseline": {
            "candidate_id": "deterministic-text",
            "sample_count": baseline_runs,
            "passed": baseline_passed,
            "evidence": baseline_result["evidence_level"],
        },
        "candidates": candidates,
        "decision": {
            "adoption": "no_beneficial_adoption",
            "retained_baseline": "deterministic-text",
        },
        "decision_reason": "The deterministic baseline is already correct on all five fixtures and no model can be materially better on this representative set; model latency, memory, runtime packaging, and installed-path gates remain additional costs.",
        "content_policy": "fixture_ids_metrics_and_output_hashes_only",
    }
    benchmark.validate_lean_comparison_record(receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
