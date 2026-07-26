#!/usr/bin/env python3
"""Reproducible, local-only benchmark contract for Mumble Issue #19.

The harness deliberately uses only the Python standard library.  It validates
the corpus, candidate inventory, and predeclared gates before any adapter is
allowed to run.  Benchmark result records never contain audio or transcript
text; content remains in the separately governed corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import wave


APPROVED_SOURCE_KINDS = frozenset({"public", "consented", "synthetic"})
REQUIRED_GATE_NAMES = frozenset(
    {
        "accuracy",
        "latency",
        "resource",
        "language",
        "safety",
        "licence",
        "windows",
        "packaging",
    }
)
REQUIRED_LICENCE_FIELDS = frozenset(
    {
        "source_licence",
        "runtime_dependency_licence",
        "weights",
        "tokenizer",
        "dataset_conversion_provenance",
        "redistribution",
        "attribution",
        "gating",
        "branding",
    }
)


class ContractError(ValueError):
    """Raised when a benchmark input is incomplete or unsafe."""


def _normalised_words(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w\s']+", " ", text, flags=re.UNICODE)
    return text.split()


def word_error_rate(reference, hypothesis):
    """Return ordinary word-level Levenshtein error rate for one fixture."""
    expected = _normalised_words(reference)
    actual = _normalised_words(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row]
        for column, actual_word in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_word != actual_word),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def speech_metrics(
    *,
    fixture_id,
    reference,
    hypothesis,
    audio_duration_ms,
    inference_ms,
    first_stable_partial_ms,
    stop_to_final_ms,
    peak_ram_mb,
    peak_vram_mb,
    model_size_bytes,
    idle_cpu_percent,
    failure_recovery,
):
    """Build one content-free speech result from in-memory scored text."""
    if not isinstance(failure_recovery, bool):
        raise ContractError("failure_recovery_boolean_required")
    duration = float(audio_duration_ms)
    if duration <= 0:
        raise ValueError("audio_duration_ms_must_be_positive")
    return {
        "fixture_id": str(fixture_id),
        "wer": word_error_rate(reference, hypothesis),
        "first_stable_partial_ms": float(first_stable_partial_ms),
        "stop_to_final_ms": float(stop_to_final_ms),
        "real_time_factor": float(inference_ms) / duration,
        "peak_ram_mb": float(peak_ram_mb),
        "peak_vram_mb": float(peak_vram_mb),
        "model_size_bytes": int(model_size_bytes),
        "idle_cpu_percent": float(idle_cpu_percent),
        "failure_recovery": failure_recovery,
    }


def validate_preliminary_speech_record(record):
    """Reject a preliminary speech receipt that could be mistaken for adoption."""
    if record.get("schema") != "mumble.local-ai-preliminary-speech.v1":
        raise ContractError("invalid_preliminary_speech_schema")
    if record.get("evidence_level") != "single_offline_synthetic_preliminary_only":
        raise ContractError("preliminary_evidence_label_required")
    if record.get("adoption_gate_status") != "not_evaluated":
        raise ContractError("preliminary_adoption_gates_must_be_not_evaluated")
    if record.get("decision") != "retain_baseline_preliminary_measurement_only":
        raise ContractError("preliminary_measurement_must_retain_baseline")
    fixture = record.get("fixture") or {}
    if fixture.get("audio_retained") is not False:
        raise ContractError("preliminary_audio_must_not_be_retained")
    if re.fullmatch(r"[0-9a-f]{64}", str(fixture.get("audio_sha256", ""))) is None:
        raise ContractError("preliminary_audio_sha256_required")
    model_files = record.get("model_files")
    if not isinstance(model_files, list) or not model_files:
        raise ContractError("preliminary_model_files_required")
    for item in model_files:
        if (
            not item.get("filename")
            or not isinstance(item.get("size_bytes"), int)
            or item["size_bytes"] <= 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is None
        ):
            raise ContractError("invalid_preliminary_model_file")
    metrics = record.get("metrics") or {}
    required_metrics = {
        "wer",
        "first_stable_partial_ms",
        "stop_to_final_ms",
        "real_time_factor",
        "peak_ram_mb",
        "peak_vram_mb",
        "model_size_bytes",
        "resident_idle_ram_mb",
        "resident_idle_cpu_percent_one_second",
        "failure_recovery",
    }
    if not required_metrics <= set(metrics):
        raise ContractError("incomplete_preliminary_metrics")
    forbidden_keys = {"audio_path", "hypothesis", "reference", "transcript"}

    def walk(value):
        if isinstance(value, dict):
            if forbidden_keys & {str(key).casefold() for key in value}:
                raise ContractError("content_bearing_preliminary_field")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(record)
    return record


def _decision_from_gate_results(*, candidate_id, baseline_id, gate_results):
    """Retain the baseline unless all eight predeclared gates pass."""
    if set(gate_results) != REQUIRED_GATE_NAMES:
        missing = sorted(REQUIRED_GATE_NAMES - set(gate_results))
        extra = sorted(set(gate_results) - REQUIRED_GATE_NAMES)
        raise ContractError(f"gate_result_key_mismatch:missing={missing}:extra={extra}")
    blocking = sorted(
        f"{name}:{status}"
        for name, status in gate_results.items()
        if status != "pass"
    )
    return {
        "candidate_id": str(candidate_id),
        "baseline_id": str(baseline_id),
        "decision": (
            "retain_baseline" if blocking else "eligible_for_integration_review"
        ),
        "blocking_gates": blocking,
    }


def _metric_at_least(metrics, name, threshold):
    value = metrics.get(name)
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= threshold
    )


def _metric_at_most(metrics, name, threshold):
    value = metrics.get(name)
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value <= threshold
    )


def _is_rate(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _is_nonnegative(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _is_nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _traceable_proof(evidence, gate, *, candidate_id, evidence_root):
    proof = (evidence.get("proofs") or {}).get(gate) or {}
    references = proof.get("evidence")
    structurally_valid = (
        set(proof) == {"status", "evidence"}
        and proof.get("status") == "pass"
        and isinstance(references, list)
        and bool(references)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("record"), str)
            and item["record"].strip()
            and re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
            for item in references
        )
    )
    verified_references = []
    root = Path(evidence_root).resolve()
    if structurally_valid:
        for item in references:
            relative = Path(item["record"])
            if relative.is_absolute():
                structurally_valid = False
                break
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                structurally_valid = False
                break
            if _sha256_file(resolved) != item["sha256"]:
                structurally_valid = False
                break
            try:
                proof_record = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                structurally_valid = False
                break
            if (
                set(proof_record)
                != {"schema", "candidate_id", "gate", "status", "artifacts"}
                or proof_record.get("schema") != "mumble.local-ai-gate-proof.v1"
                or proof_record.get("candidate_id") != candidate_id
                or proof_record.get("gate") != gate
                or proof_record.get("status") != "pass"
                or not isinstance(proof_record.get("artifacts"), list)
                or not proof_record["artifacts"]
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in proof_record["artifacts"]
                )
            ):
                structurally_valid = False
                break
            verified_references.append(dict(item))
    return ("pass" if structurally_valid else "fail"), {
        "source": "traceable_proof",
        "references": verified_references,
    }


def evaluate_candidate_evidence(*, candidate, lane, evidence, evidence_root):
    """Derive all eight gates from metrics and traceable proof references."""
    metrics = evidence.get("metrics") or {}
    thresholds = lane.get("thresholds") or {}
    lane_id = candidate["lane"]
    gate_results = {}
    gate_evidence = {}

    if lane_id == "instruction-text":
        if set(metrics) != {"modes"}:
            raise ContractError("invalid_instruction_metric_schema")
        required_modes = set(lane.get("required_modes") or ())
        modes = metrics.get("modes") if isinstance(metrics.get("modes"), dict) else {}
        complete_modes = set(modes) == required_modes
        required_fields = {
            "corpus_runs",
            "task_success_rate",
            "semantic_preservation_rate",
            "unsupported_additions",
            "first_token_ms",
            "completion_p95_ms",
            "peak_ram_mb",
            "peak_vram_mb",
            "model_size_bytes",
            "idle_cpu_percent",
            "failure_recovery_rate",
        }
        if any(
            isinstance(mode_metrics, dict)
            and not set(mode_metrics) <= required_fields
            for mode_metrics in modes.values()
        ):
            raise ContractError("invalid_instruction_metric_schema")
        fields_complete = complete_modes and all(
            isinstance(mode_metrics, dict) and set(mode_metrics) == required_fields
            for mode_metrics in modes.values()
        )
        domains_valid = fields_complete and all(
            _is_nonnegative_int(item["corpus_runs"])
            and _is_rate(item["task_success_rate"])
            and _is_rate(item["semantic_preservation_rate"])
            and _is_nonnegative_int(item["unsupported_additions"])
            and _is_nonnegative(item["first_token_ms"])
            and _is_nonnegative(item["completion_p95_ms"])
            and _is_nonnegative(item["peak_ram_mb"])
            and _is_nonnegative(item["peak_vram_mb"])
            and _is_nonnegative_int(item["model_size_bytes"])
            and item["model_size_bytes"] > 0
            and _is_nonnegative(item["idle_cpu_percent"])
            and _is_rate(item["failure_recovery_rate"])
            for item in modes.values()
        )
        accuracy_pass = domains_valid and all(
            _metric_at_least(item, "corpus_runs", thresholds["corpus_runs_min"])
            and _metric_at_least(
                item, "task_success_rate", thresholds["task_success_rate_min"]
            )
            and _metric_at_least(
                item,
                "semantic_preservation_rate",
                thresholds["semantic_preservation_rate_min"],
            )
            and _metric_at_most(
                item, "unsupported_additions", thresholds["unsupported_additions_max"]
            )
            for item in modes.values()
        )
        latency_pass = domains_valid and all(
            _metric_at_most(
                item, "completion_p95_ms", thresholds["completion_p95_ms_max"]
            )
            for item in modes.values()
        )
        resource_pass = domains_valid and all(
            _metric_at_most(item, "peak_ram_mb", thresholds["peak_ram_mb_max"])
            and _metric_at_most(
                item, "idle_cpu_percent", thresholds["idle_cpu_percent_max"]
            )
            and _metric_at_least(
                item,
                "failure_recovery_rate",
                thresholds["failure_recovery_rate_min"],
            )
            and _metric_at_least(item, "model_size_bytes", 1)
            and _metric_at_least(item, "peak_vram_mb", 0)
            for item in modes.values()
        )
        gate_evidence.update(
            {
                "accuracy": {"source": "metrics.modes", "required_modes": sorted(required_modes)},
                "latency": {"source": "metrics.modes.completion_p95_ms"},
                "resource": {"source": "metrics.modes.resource_and_recovery"},
            }
        )
    elif lane_id == "classification":
        required_fields = {
            "corpus_runs",
            "macro_f1",
            "false_action_count",
            "abstention_accuracy",
            "latency_p95_ms",
            "peak_ram_mb",
            "peak_vram_mb",
            "model_size_bytes",
            "idle_cpu_percent",
            "failure_recovery_rate",
        }
        if not set(metrics) <= required_fields:
            raise ContractError("invalid_classification_metric_schema")
        fields_complete = set(metrics) == required_fields
        domains_valid = fields_complete and (
            _is_nonnegative_int(metrics["corpus_runs"])
            and _is_rate(metrics["macro_f1"])
            and _is_nonnegative_int(metrics["false_action_count"])
            and _is_rate(metrics["abstention_accuracy"])
            and _is_nonnegative(metrics["latency_p95_ms"])
            and _is_nonnegative(metrics["peak_ram_mb"])
            and _is_nonnegative(metrics["peak_vram_mb"])
            and _is_nonnegative_int(metrics["model_size_bytes"])
            and metrics["model_size_bytes"] > 0
            and _is_nonnegative(metrics["idle_cpu_percent"])
            and _is_rate(metrics["failure_recovery_rate"])
        )
        accuracy_pass = domains_valid and all(
            (
                _metric_at_least(metrics, "corpus_runs", thresholds["corpus_runs_min"]),
                _metric_at_least(metrics, "macro_f1", thresholds["macro_f1_min"]),
                _metric_at_most(
                    metrics, "false_action_count", thresholds["false_action_count_max"]
                ),
                _metric_at_least(
                    metrics,
                    "abstention_accuracy",
                    thresholds["abstention_accuracy_min"],
                ),
            )
        )
        latency_pass = domains_valid and _metric_at_most(
            metrics, "latency_p95_ms", thresholds["latency_p95_ms_max"]
        )
        resource_pass = domains_valid and all(
            (
                _metric_at_most(metrics, "peak_ram_mb", thresholds["peak_ram_mb_max"]),
                _metric_at_most(
                    metrics, "idle_cpu_percent", thresholds["idle_cpu_percent_max"]
                ),
                _metric_at_least(
                    metrics,
                    "failure_recovery_rate",
                    thresholds["failure_recovery_rate_min"],
                ),
                _metric_at_least(metrics, "model_size_bytes", 1),
                _metric_at_least(metrics, "peak_vram_mb", 0),
            )
        )
        gate_evidence.update(
            {
                "accuracy": {"source": "metrics.classification_quality"},
                "latency": {"source": "metrics.latency_p95_ms"},
                "resource": {"source": "metrics.resource_and_recovery"},
            }
        )
    else:
        required_fields = {
            "corpus_runs",
            "wer",
            "first_stable_partial_p95_ms",
            "stop_to_final_p95_ms",
            "real_time_factor_p95",
            "peak_ram_mb",
            "peak_vram_mb",
            "model_size_bytes",
            "idle_cpu_percent",
            "failure_recovery_rate",
        }
        if lane_id == "streaming-speech":
            required_fields.add("wer_absolute_degradation")
        elif lane_id == "speech-quality-ceiling":
            required_fields.add("wer_relative_improvement")
        if not set(metrics) <= required_fields:
            raise ContractError("invalid_speech_metric_schema")
        fields_complete = set(metrics) == required_fields
        domains_valid = fields_complete and (
            _is_nonnegative_int(metrics["corpus_runs"])
            and _is_rate(metrics["wer"])
            and _is_nonnegative(metrics["first_stable_partial_p95_ms"])
            and _is_nonnegative(metrics["stop_to_final_p95_ms"])
            and _is_nonnegative(metrics["real_time_factor_p95"])
            and _is_nonnegative(metrics["peak_ram_mb"])
            and _is_nonnegative(metrics["peak_vram_mb"])
            and _is_nonnegative_int(metrics["model_size_bytes"])
            and metrics["model_size_bytes"] > 0
            and _is_nonnegative(metrics["idle_cpu_percent"])
            and _is_rate(metrics["failure_recovery_rate"])
        )
        if lane_id == "streaming-speech":
            domains_valid = domains_valid and _is_nonnegative(
                metrics["wer_absolute_degradation"]
            )
        elif lane_id == "speech-quality-ceiling":
            domains_valid = domains_valid and _is_rate(
                metrics["wer_relative_improvement"]
            )
        if lane_id == "streaming-speech":
            accuracy_pass = domains_valid and _metric_at_most(
                metrics,
                "wer_absolute_degradation",
                thresholds["wer_absolute_degradation_max"],
            )
            latency_pass = domains_valid and all(
                (
                    _metric_at_most(
                        metrics,
                        "first_stable_partial_p95_ms",
                        thresholds["first_stable_partial_p95_ms_max"],
                    ),
                    _metric_at_most(
                        metrics,
                        "stop_to_final_p95_ms",
                        thresholds["stop_to_final_p95_ms_max"],
                    ),
                    _metric_at_most(
                        metrics,
                        "real_time_factor_p95",
                        thresholds["real_time_factor_p95_max"],
                    ),
                )
            )
        elif lane_id == "speech-quality-ceiling":
            accuracy_pass = domains_valid and _metric_at_least(
                metrics,
                "wer_relative_improvement",
                thresholds["wer_relative_improvement_min"],
            )
            latency_pass = domains_valid and all(
                (
                    _metric_at_most(
                        metrics,
                        "stop_to_final_p95_ms",
                        thresholds["stop_to_final_p95_ms_max"],
                    ),
                    _metric_at_most(
                        metrics,
                        "real_time_factor_p95",
                        thresholds["real_time_factor_p95_max"],
                    ),
                )
            )
        else:
            accuracy_pass = domains_valid
            latency_pass = domains_valid
        resource_pass = domains_valid and all(
            (
                _metric_at_least(metrics, "corpus_runs", thresholds["corpus_runs_min"]),
                _metric_at_least(
                    metrics,
                    "failure_recovery_rate",
                    thresholds["failure_recovery_rate_min"],
                ),
                _metric_at_least(metrics, "model_size_bytes", 1),
                _metric_at_least(metrics, "peak_ram_mb", 0),
                _metric_at_least(metrics, "peak_vram_mb", 0),
            )
        )
        for metric_name, threshold_name in (
            ("peak_ram_mb", "peak_ram_mb_max"),
            ("peak_vram_mb", "peak_vram_mb_max"),
            ("idle_cpu_percent", "idle_cpu_percent_max"),
        ):
            if threshold_name in thresholds:
                resource_pass = resource_pass and _metric_at_most(
                    metrics, metric_name, thresholds[threshold_name]
                )
        gate_evidence.update(
            {
                "accuracy": {"source": "metrics.speech_quality"},
                "latency": {"source": "metrics.speech_latency"},
                "resource": {"source": "metrics.speech_resource_and_recovery"},
            }
        )

    gate_results.update(
        {
            "accuracy": "pass" if accuracy_pass else "fail",
            "latency": "pass" if latency_pass else "fail",
            "resource": "pass" if resource_pass else "fail",
        }
    )
    for gate in ("language", "safety", "licence", "windows", "packaging"):
        gate_results[gate], gate_evidence[gate] = _traceable_proof(
            evidence,
            gate,
            candidate_id=candidate["id"],
            evidence_root=evidence_root,
        )
    return gate_results, gate_evidence


def completed_candidate_result(
    *, candidate, lane, source_commit, hardware_id, evidence, evidence_root
):
    """Build a completed result whose gates are derived, never asserted."""
    if evidence.get("status") != "completed":
        raise ContractError("completed_candidate_status_required")
    if set(evidence) != {"status", "metrics", "proofs"} or set(
        evidence.get("proofs") or {}
    ) != {"language", "safety", "licence", "windows", "packaging"}:
        raise ContractError("invalid_completed_evidence_schema")
    gate_results, gate_evidence = evaluate_candidate_evidence(
        candidate=candidate,
        lane=lane,
        evidence=evidence,
        evidence_root=evidence_root,
    )
    decision = _decision_from_gate_results(
        candidate_id=candidate["id"],
        baseline_id=lane["baseline"],
        gate_results=gate_results,
    )
    return {
        "schema": "mumble.local-ai-benchmark-result.v1",
        "candidate_id": candidate["id"],
        "baseline_id": lane["baseline"],
        "source_commit": str(source_commit),
        "hardware_id": str(hardware_id),
        "status": "completed",
        "metrics": evidence["metrics"],
        "gate_results": gate_results,
        "gate_evidence": gate_evidence,
        "decision": decision,
        "content_policy": "fixture_ids_and_metrics_only",
    }


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _https_fetch(url, destination, max_bytes):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ContractError("artifact_url_must_be_https")
    request = urllib.request.Request(str(url), headers={"User-Agent": "Mumble-Issue19/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        final_url = urllib.parse.urlparse(str(response.geturl()))
        if final_url.scheme != "https" or not final_url.netloc:
            raise ContractError("artifact_redirect_must_remain_https")
        downloaded = 0
        with Path(destination).open("wb") as output:
            while True:
                block = response.read(min(1024 * 1024, max_bytes - downloaded + 1))
                if not block:
                    break
                downloaded += len(block)
                if downloaded > max_bytes:
                    raise ContractError("artifact_download_exceeds_expected_size")
                output.write(block)
            output.flush()
            os.fsync(output.fileno())


def prepare_artifact(
    *, cache_root, artifact, offline, baseline_id, fetcher=None
):
    """Resolve one exact artifact without silently weakening to unknown bytes.

    A valid existing entry is reused offline.  A missing or corrupt offline entry
    reports the declared baseline fallback.  Online recovery downloads to a
    temporary sibling, verifies size and SHA-256, then atomically replaces the
    cache entry.
    """
    root = Path(cache_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = str(artifact.get("filename", ""))
    if not filename or Path(filename).name != filename or any(
        separator in filename for separator in ("/", "\\")
    ):
        raise ContractError("unsafe_artifact_filename")
    expected_sha = str(artifact.get("sha256", "")).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        raise ContractError("artifact_sha256_required")
    expected_size = artifact.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ContractError("artifact_size_required")

    destination = root / filename
    existing_corrupt = False
    if destination.is_file():
        existing_corrupt = (
            destination.stat().st_size != expected_size
            or _sha256_file(destination) != expected_sha
        )
        if not existing_corrupt:
            return {
                "artifact_id": artifact.get("id"),
                "status": "ready",
                "sha256": expected_sha,
                "size_bytes": expected_size,
                "recovered_corrupt_cache": False,
                "fallback": None,
            }

    if offline:
        return {
            "artifact_id": artifact.get("id"),
            "status": "corrupt" if existing_corrupt else "missing",
            "sha256": expected_sha,
            "size_bytes": expected_size,
            "recovered_corrupt_cache": False,
            "fallback": str(baseline_id),
        }

    temporary = destination.with_name(destination.name + ".part")
    try:
        if temporary.exists():
            temporary.unlink()
        (fetcher or _https_fetch)(artifact.get("url"), temporary, expected_size)
        actual_size = temporary.stat().st_size
        actual_sha = _sha256_file(temporary)
        if actual_size != expected_size or actual_sha != expected_sha:
            raise ContractError(
                f"artifact_verification_failed:size={actual_size}:sha256={actual_sha}"
            )
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass

    return {
        "artifact_id": artifact.get("id"),
        "status": "ready",
        "sha256": expected_sha,
        "size_bytes": expected_size,
        "recovered_corrupt_cache": existing_corrupt,
        "fallback": None,
    }


def _percentile(values, proportion):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * float(proportion)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _text_fixture_passed(item, output):
    folded = str(output).casefold()
    required = [str(value).casefold() for value in item.get("required_literals", ())]
    forbidden = [str(value).casefold() for value in item.get("forbidden_literals", ())]
    return all(value in folded for value in required) and not any(
        value in folded for value in forbidden
    )


def run_deterministic_baseline(*, contract, app_dir, source_commit, hardware_id):
    """Run Mumble's model-free Prompt/Email/Reply/classification controls."""
    app_dir = Path(app_dir).resolve()
    inserted = False
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
        inserted = True
    try:
        import formatting
        import voice_commands

        by_lane = {}
        for item in contract["corpus"]["items"]:
            lane = item.get("lane")
            if lane not in {"prompt", "email", "reply", "classification"}:
                continue
            false_action = 0
            started = time.perf_counter_ns()
            if lane == "prompt":
                output = formatting.build_prompt(item["input"])
                passed = _text_fixture_passed(item, output)
            elif lane == "email":
                output = formatting.build_email(item["input"])
                passed = _text_fixture_passed(item, output)
            elif lane == "reply":
                output = formatting.format_transcript(item["input"], commands=False)
                passed = _text_fixture_passed(item, output)
            else:
                command = voice_commands.parse_voice_command(item["input"])
                expected = item["expected"]
                passed = (
                    command.intent == expected["intent"]
                    and command.value == expected["value"]
                )
                actual_action = command.intent != "control"
                expected_action = expected["intent"] != "control"
                false_action = int(actual_action and (not expected_action or not passed))
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            lane_result = by_lane.setdefault(
                lane,
                {
                    "fixture_ids": [],
                    "passed": 0,
                    "latency_ms": [],
                    "false_action_count": 0,
                },
            )
            lane_result["fixture_ids"].append(item["id"])
            lane_result["passed"] += int(passed)
            lane_result["latency_ms"].append(elapsed_ms)
            lane_result["false_action_count"] += false_action
    finally:
        if inserted:
            sys.path.remove(str(app_dir))

    lanes = {}
    for lane, raw in sorted(by_lane.items()):
        runs = len(raw["fixture_ids"])
        lane_summary = {
            "fixture_ids": raw["fixture_ids"],
            "runs": runs,
            "task_success_rate": raw["passed"] / runs,
            "latency_p50_ms": round(_percentile(raw["latency_ms"], 0.50), 3),
            "latency_p95_ms": round(_percentile(raw["latency_ms"], 0.95), 3),
            "latency_max_ms": round(max(raw["latency_ms"]), 3),
            "failure_recovery": "not_run",
        }
        if lane == "classification":
            lane_summary.update(
                {
                    "false_action_count": raw["false_action_count"],
                    "execution_authority": False,
                }
            )
        lanes[lane] = lane_summary

    status = (
        "completed"
        if lanes and all(lane["task_success_rate"] == 1.0 for lane in lanes.values())
        else "failed"
    )
    return {
        "schema": "mumble.local-ai-benchmark-result.v1",
        "contract_version": contract["corpus"]["version"],
        "candidate_id": "deterministic-text",
        "baseline_for": ["deterministic-text", "instruction-text", "classification"],
        "source_commit": str(source_commit),
        "hardware_id": str(hardware_id),
        "status": status,
        "evidence_level": "harness_smoke_only",
        "adoption_gate_status": "not_evaluated",
        "lanes": lanes,
        "content_policy": "fixture_ids_and_metrics_only",
    }


def not_run_result(
    *,
    candidate_id,
    baseline_id,
    source_commit,
    hardware_id,
    reasons,
    gate_results,
):
    """Record an unavailable lane without manufacturing benchmark evidence."""
    cleaned_reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]
    if not cleaned_reasons:
        raise ContractError("not_run_reason_required")
    decision = _decision_from_gate_results(
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        gate_results=gate_results,
    )
    if decision["decision"] != "retain_baseline":
        raise ContractError("not_run_candidate_cannot_be_eligible")
    return {
        "schema": "mumble.local-ai-benchmark-result.v1",
        "candidate_id": str(candidate_id),
        "baseline_id": str(baseline_id),
        "source_commit": str(source_commit),
        "hardware_id": str(hardware_id),
        "status": "not_run",
        "reasons": cleaned_reasons,
        "gate_results": dict(gate_results),
        "decision": decision,
        "metrics": None,
        "content_policy": "no_audio_or_transcript_content",
    }


def build_ledger(
    *, contract, app_dir, source_parent, hardware, availability_record, evidence_root
):
    """Build one host-specific, content-free Issue #19 evidence ledger."""
    if hardware.get("schema") != "mumble.local-ai-hardware.v1" or not hardware.get("id"):
        raise ContractError("invalid_hardware_record")
    if availability_record.get("schema") != "mumble.local-ai-availability.v1":
        raise ContractError("invalid_availability_record")
    if availability_record.get("source_parent") != str(source_parent):
        raise ContractError("availability_source_parent_mismatch")
    if availability_record.get("hardware_id") != hardware["id"]:
        raise ContractError("availability_hardware_mismatch")
    availability = availability_record.get("candidate_availability")
    if not isinstance(availability, dict):
        raise ContractError("invalid_candidate_availability")
    inventory = contract["candidates"]["candidates"]
    expected_unavailable = {
        candidate["id"] for candidate in inventory
        if candidate["id"] != "deterministic-text"
    }
    if set(availability) != expected_unavailable:
        missing = sorted(expected_unavailable - set(availability))
        extra = sorted(set(availability) - expected_unavailable)
        raise ContractError(f"availability_key_mismatch:missing={missing}:extra={extra}")

    results = []
    for candidate in inventory:
        candidate_id = candidate["id"]
        if candidate_id == "deterministic-text":
            results.append(
                run_deterministic_baseline(
                    contract=contract,
                    app_dir=app_dir,
                    source_commit=source_parent,
                    hardware_id=hardware["id"],
                )
            )
            continue
        lane = contract["gates"]["lanes"][candidate["lane"]]
        evidence = availability[candidate_id]
        if evidence.get("status") == "completed":
            result = completed_candidate_result(
                candidate=candidate,
                lane=lane,
                source_commit=source_parent,
                hardware_id=hardware["id"],
                evidence=evidence,
                evidence_root=evidence_root,
            )
        else:
            result = not_run_result(
                candidate_id=candidate_id,
                baseline_id=lane["baseline"],
                source_commit=source_parent,
                hardware_id=hardware["id"],
                reasons=evidence["reasons"],
                gate_results=evidence["gate_results"],
            )
        preliminary_result = evidence.get("preliminary_result")
        if preliminary_result:
            preliminary_result = str(preliminary_result)
            if Path(preliminary_result).name != preliminary_result:
                raise ContractError("unsafe_preliminary_result_filename")
            result["preliminary_result_file"] = preliminary_result
        results.append(result)

    eligible = [
        result["candidate_id"] for result in results
        if result.get("decision", {}).get("decision")
        == "eligible_for_integration_review"
    ]
    return {
        "schema": "mumble.local-ai-benchmark-ledger.v1",
        "contract_version": contract["corpus"]["version"],
        "gate_version": contract["gates"]["version"],
        "candidate_inventory_version": contract["candidates"]["version"],
        "source_parent": str(source_parent),
        "hardware": hardware,
        "candidate_results": results,
        "eligible_candidates": eligible,
        "adoption": "candidate_review_required" if eligible else "no_candidate_adopted",
        "retained_baselines": ["deterministic-text", "faster-whisper"],
        "content_policy": "fixture_ids_and_metrics_only",
    }


def _generate_windows_sapi_wav(*, destination, text, voice_description):
    if os.name != "nt":
        raise ContractError("windows_sapi_requires_windows")
    import base64

    encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = r"""
$ErrorActionPreference = 'Stop'
$destination = $env:MUMBLE_SAPI_DESTINATION
$description = $env:MUMBLE_SAPI_VOICE
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MUMBLE_SAPI_TEXT))
$voice = New-Object -ComObject SAPI.SpVoice
$stream = New-Object -ComObject SAPI.SpFileStream
try {
  $selected = $voice.GetVoices() | Where-Object { $_.GetDescription() -eq $description } | Select-Object -First 1
  if ($null -eq $selected) { throw 'requested_sapi_voice_not_found' }
  $voice.Voice = $selected
  $voice.Rate = 0
  $voice.Volume = 100
  $stream.Format.Type = 22
  $stream.Open($destination, 3, $false)
  $voice.AudioOutputStream = $stream
  [void]$voice.Speak($text)
  $stream.Close()
  Write-Output $voice.Voice.GetDescription()
} finally {
  try { $stream.Close() } catch {}
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($voice)
}
"""
    environment = os.environ.copy()
    environment.update(
        {
            "MUMBLE_SAPI_DESTINATION": str(Path(destination).resolve()),
            "MUMBLE_SAPI_VOICE": str(voice_description),
            "MUMBLE_SAPI_TEXT": encoded_text,
        }
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    observed_voice = completed.stdout.strip()
    if observed_voice != voice_description or not Path(destination).is_file():
        raise ContractError("sapi_fixture_generation_failed")
    return observed_voice


def validate_cached_faster_whisper_snapshot(*, model_dir, expected_snapshot):
    """Bind a local cache directory to one declared small.en revision and manifest."""
    model_dir = Path(model_dir).resolve()
    if (
        expected_snapshot.get("schema") != "mumble.local-ai-preliminary-speech.v1"
        or expected_snapshot.get("candidate_id") != "faster-whisper"
        or expected_snapshot.get("model") != "Systran/faster-whisper-small.en"
    ):
        raise ContractError("unexpected_cached_model_identity")
    revision = str(expected_snapshot.get("snapshot_revision", ""))
    if not revision or model_dir.name != revision:
        raise ContractError("cached_model_revision_mismatch")
    expected_files = expected_snapshot.get("model_files")
    if not isinstance(expected_files, list) or not expected_files:
        raise ContractError("cached_model_manifest_required")
    declared = {}
    for item in expected_files:
        if (
            set(item) != {"filename", "size_bytes", "sha256"}
            or Path(str(item.get("filename", ""))).name != item.get("filename")
            or not isinstance(item.get("size_bytes"), int)
            or item["size_bytes"] <= 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is None
        ):
            raise ContractError("invalid_cached_model_manifest_entry")
        declared[item["filename"]] = item
    if not {"config.json", "model.bin", "tokenizer.json"} <= set(declared):
        raise ContractError("incomplete_cached_faster_whisper_snapshot")
    actual_names = {path.name for path in model_dir.iterdir() if path.is_file()}
    if actual_names != set(declared):
        raise ContractError("cached_model_file_set_mismatch")
    observed = []
    for filename in sorted(declared):
        path = model_dir / filename
        item = declared[filename]
        if path.stat().st_size != item["size_bytes"] or _sha256_file(path) != item["sha256"]:
            raise ContractError(f"cached_model_file_mismatch:{filename}")
        observed.append(dict(item))
    return {
        "model": expected_snapshot["model"],
        "snapshot_revision": revision,
        "model_files": observed,
    }


def run_preliminary_faster_whisper(
    *,
    contract,
    model_dir,
    expected_snapshot,
    hardware,
    source_parent,
    fixture_id,
    voice_description,
):
    """Run one cached-only SAPI fixture and return a content-free receipt."""
    model_dir = Path(model_dir).resolve()
    if not model_dir.is_dir():
        raise ContractError("cached_model_directory_required")
    if hardware.get("schema") != "mumble.local-ai-hardware.v1" or not hardware.get("id"):
        raise ContractError("invalid_hardware_record")
    fixture = next(
        (item for item in contract["corpus"]["items"] if item.get("id") == fixture_id),
        None,
    )
    if (
        fixture is None
        or fixture.get("lane") != "speech"
        or (fixture.get("source") or {}).get("kind") != "synthetic"
        or not fixture.get("reference")
    ):
        raise ContractError("approved_synthetic_speech_fixture_required")

    identity = validate_cached_faster_whisper_snapshot(
        model_dir=model_dir,
        expected_snapshot=expected_snapshot,
    )
    model_files = identity["model_files"]

    try:
        import psutil
    except ImportError as exc:
        raise ContractError(f"preliminary_runtime_missing:{exc.name}") from exc
    offline_keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    previous_environment = {key: os.environ.get(key) for key in offline_keys}

    process = psutil.Process()
    peak_rss = process.memory_info().rss
    stop_sampling = threading.Event()

    def sample_memory():
        nonlocal peak_rss
        while not stop_sampling.wait(0.01):
            peak_rss = max(peak_rss, process.memory_info().rss)

    with tempfile.TemporaryDirectory(prefix="mumble-issue19-sapi-") as temporary_root:
        wav_path = Path(temporary_root) / "synthetic.wav"
        observed_voice = _generate_windows_sapi_wav(
            destination=wav_path,
            text=fixture["reference"],
            voice_description=voice_description,
        )
        with wave.open(str(wav_path), "rb") as source:
            frames = source.getnframes()
            rate = source.getframerate()
            audio_metadata = {
                "channels": source.getnchannels(),
                "sample_width_bytes": source.getsampwidth(),
                "sample_rate_hz": rate,
                "duration_ms": round(frames * 1000 / rate, 3),
            }

        sampler = threading.Thread(target=sample_memory, daemon=True)
        sampler.start()
        try:
            for key in offline_keys:
                os.environ[key] = "1"
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ContractError(f"preliminary_runtime_missing:{exc.name}") from exc
            load_started = time.perf_counter()
            model = WhisperModel(
                str(model_dir),
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                num_workers=1,
                local_files_only=True,
            )
            model_load_ms = (time.perf_counter() - load_started) * 1000
            process.cpu_percent(None)
            time.sleep(1.0)
            idle_cpu_percent = process.cpu_percent(None)
            idle_ram_mb = process.memory_info().rss / (1024 * 1024)
            inference_started = time.perf_counter()
            segments, _info = model.transcribe(
                str(wav_path),
                language="en",
                beam_size=1,
                vad_filter=False,
                temperature=0.0,
                word_timestamps=False,
            )
            hypothesis = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            )
            inference_ms = (time.perf_counter() - inference_started) * 1000
            audio_sha256 = _sha256_file(wav_path)
        finally:
            stop_sampling.set()
            sampler.join(timeout=1)
            for key, previous in previous_environment.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    result = {
        "schema": "mumble.local-ai-preliminary-speech.v1",
        "observed_at": time.strftime("%Y-%m-%d"),
        "source_parent": str(source_parent),
        "hardware_id": hardware["id"],
        "evidence_level": "single_offline_synthetic_preliminary_only",
        "adoption_gate_status": "not_evaluated",
        "candidate_id": "faster-whisper",
        "model": identity["model"],
        "snapshot_revision": identity["snapshot_revision"],
        "snapshot_location_policy": "local_cache_path_not_recorded",
        "model_files": model_files,
        "runtime": {
            "faster_whisper": importlib.metadata.version("faster-whisper"),
            "ctranslate2": importlib.metadata.version("ctranslate2"),
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 4,
            "num_workers": 1,
            "beam_size": 1,
            "language": "en",
            "vad_filter": False,
            "network_disabled": True,
        },
        "fixture": {
            "id": fixture_id,
            "source": "Windows SAPI synthetic speech",
            "voice": observed_voice,
            "voice_rate": 0,
            "voice_volume": 100,
            "audio_sha256": audio_sha256,
            **audio_metadata,
            "audio_retained": False,
        },
        "metrics": {
            "wer": word_error_rate(fixture["reference"], hypothesis),
            "first_stable_partial_ms": None,
            "first_stable_partial_reason": (
                "not_available_from_completed_utterance_faster_whisper_baseline"
            ),
            "stop_to_final_ms": round(inference_ms, 3),
            "real_time_factor": round(inference_ms / audio_metadata["duration_ms"], 6),
            "model_load_ms": round(model_load_ms, 3),
            "peak_ram_mb": round(peak_rss / (1024 * 1024), 3),
            "peak_vram_mb": 0.0,
            "model_size_bytes": sum(item["size_bytes"] for item in model_files),
            "resident_idle_ram_mb": round(idle_ram_mb, 3),
            "resident_idle_cpu_percent_one_second": idle_cpu_percent,
            "failure_recovery": "not_run",
        },
        "limitations": [
            "one_synthetic_fixture_only",
            "no_first_stable_partial_from_completed_utterance_baseline",
            "no_failure_injection",
            "no_thirty_run_cold_warm_matrix",
            "no_installed_package_measurement",
            "no_physical_microphone_or_owner_recording",
        ],
        "content_policy": "fixture_id_hash_and_metrics_only",
        "decision": "retain_baseline_preliminary_measurement_only",
    }
    validate_preliminary_speech_record(result)
    return result


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot_read_contract:{path.name}:{exc}") from exc


def load_contract(root):
    """Load and strictly validate the version-1 benchmark authority."""
    root = Path(root).resolve()
    corpus = _read_json(root / "corpus-v1.json")
    gates = _read_json(root / "gates-v1.json")
    candidates = _read_json(root / "candidates-v1.json")

    if corpus.get("schema") != "mumble.local-ai-corpus.v1":
        raise ContractError("unsupported_corpus_schema")
    if gates.get("schema") != "mumble.local-ai-gates.v1":
        raise ContractError("unsupported_gates_schema")
    if candidates.get("schema") != "mumble.local-ai-candidates.v1":
        raise ContractError("unsupported_candidates_schema")

    items = corpus.get("items")
    if not isinstance(items, list) or not items:
        raise ContractError("empty_corpus")
    seen = set()
    for item in items:
        fixture_id = str(item.get("id", ""))
        if not fixture_id or fixture_id in seen:
            raise ContractError("invalid_or_duplicate_fixture_id")
        seen.add(fixture_id)
        source = item.get("source") or {}
        if source.get("kind") not in APPROVED_SOURCE_KINDS:
            raise ContractError(f"unapproved_source:{fixture_id}")
        if source.get("kind") == "consented" and not source.get("consent_record"):
            raise ContractError(f"missing_consent_record:{fixture_id}")
        if source.get("kind") == "public" and not source.get("source_url"):
            raise ContractError(f"missing_public_source:{fixture_id}")

    lane_map = gates.get("lanes")
    if not isinstance(lane_map, dict) or not lane_map:
        raise ContractError("missing_gate_lanes")
    for lane_id, lane in lane_map.items():
        if set(lane.get("required_gates", ())) != REQUIRED_GATE_NAMES:
            raise ContractError(f"incomplete_required_gates:{lane_id}")
        if lane_id == "instruction-text" and set(lane.get("required_modes", ())) != {
            "prompt",
            "email",
            "reply",
            "classification",
        }:
            raise ContractError("incomplete_instruction_modes")

    inventory = candidates.get("candidates")
    if not isinstance(inventory, list) or not inventory:
        raise ContractError("empty_candidate_inventory")
    candidate_ids = set()
    for candidate in inventory:
        candidate_id = str(candidate.get("id", ""))
        if not candidate_id or candidate_id in candidate_ids:
            raise ContractError("invalid_or_duplicate_candidate_id")
        candidate_ids.add(candidate_id)
        lane = candidate.get("lane")
        if lane not in lane_map:
            raise ContractError(f"unknown_candidate_lane:{candidate_id}")
        if set((candidate.get("licence") or {}).keys()) != REQUIRED_LICENCE_FIELDS:
            raise ContractError(f"incomplete_licence_record:{candidate_id}")

    return {"root": root, "corpus": corpus, "gates": gates, "candidates": candidates}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mumble local-AI benchmark harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate versioned inputs")
    validate_parser.add_argument("contract_root", type=Path)
    run_parser = subparsers.add_parser("run", help="run safe baselines and record unavailable lanes")
    run_parser.add_argument("contract_root", type=Path)
    run_parser.add_argument("--app-dir", type=Path, required=True)
    run_parser.add_argument("--hardware", type=Path, required=True)
    run_parser.add_argument("--availability", type=Path, required=True)
    run_parser.add_argument("--source-parent", required=True)
    preliminary_parser = subparsers.add_parser(
        "preliminary-faster-whisper",
        help="run one cached-only Windows SAPI baseline fixture",
    )
    preliminary_parser.add_argument("contract_root", type=Path)
    preliminary_parser.add_argument("--model-dir", type=Path, required=True)
    preliminary_parser.add_argument("--expected-snapshot", type=Path, required=True)
    preliminary_parser.add_argument("--hardware", type=Path, required=True)
    preliminary_parser.add_argument("--source-parent", required=True)
    preliminary_parser.add_argument(
        "--fixture-id", default="speech-dictation-001"
    )
    preliminary_parser.add_argument("--voice-description", required=True)
    args = parser.parse_args(argv)
    contract = load_contract(args.contract_root)
    if args.command == "validate":
        output = {
            "ok": True,
            "corpus_version": contract["corpus"]["version"],
            "fixtures": len(contract["corpus"]["items"]),
            "candidates": len(contract["candidates"]["candidates"]),
        }
    elif args.command == "run":
        hardware = _read_json(args.hardware.resolve())
        availability_record = _read_json(args.availability.resolve())
        if availability_record.get("schema") != "mumble.local-ai-availability.v1":
            raise ContractError("unsupported_availability_schema")
        output = build_ledger(
            contract=contract,
            app_dir=args.app_dir,
            source_parent=args.source_parent,
            hardware=hardware,
            availability_record=availability_record,
            evidence_root=args.availability.resolve().parent,
        )
    else:
        output = run_preliminary_faster_whisper(
            contract=contract,
            model_dir=args.model_dir,
            expected_snapshot=_read_json(args.expected_snapshot.resolve()),
            hardware=_read_json(args.hardware.resolve()),
            source_parent=args.source_parent,
            fixture_id=args.fixture_id,
            voice_description=args.voice_description,
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
