import copy
import io
import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "Development Files" / "Tooling" / "local_ai_benchmark.py"
CONTRACT_ROOT = (
    ROOT / "Development Files" / "Research" / "local-ai-benchmark" / "v1"
)
PRELIMINARY_SPEECH_RESULT = (
    ROOT
    / "Development Files"
    / "Research"
    / "local-ai-benchmark"
    / "runs"
    / "2026-07-26-windows-z1"
    / "preliminary-faster-whisper-small.en.json"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("local_ai_benchmark", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hash_verified_receipt_parses_the_exact_hashed_bytes(
    tmp_path, monkeypatch
):
    benchmark = _load_tool()
    receipt = tmp_path / "receipt.json"
    original = {"value": "original"}
    swapped = {"value": "swapped-after-hash"}
    original_bytes = json.dumps(original, sort_keys=True).encode("utf-8")
    receipt.write_bytes(original_bytes)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    def swap_after_hash(path):
        hashed = Path(path).read_bytes()
        Path(path).write_text(json.dumps(swapped), encoding="utf-8")
        return hashlib.sha256(hashed).hexdigest()

    monkeypatch.setattr(benchmark, "_sha256_file", swap_after_hash)
    opened, _reference = benchmark._open_hashed_json_reference(
        {"record": receipt.name, "sha256": expected_sha},
        evidence_root=tmp_path,
        error_prefix="receipt",
    )

    assert opened == original


def _passing_instruction_evidence(proof_root, candidate_id="qwen3-0.6b-q8"):
    modes = {}
    for mode in ("prompt", "email", "reply", "classification"):
        modes[mode] = {
            "corpus_runs": 30,
            "task_success_rate": 1.0,
            "semantic_preservation_rate": 1.0,
            "unsupported_additions": 0,
            "first_token_ms": 100,
            "completion_p95_ms": 1200,
            "peak_ram_mb": 900,
            "peak_vram_mb": 0,
            "model_size_bytes": 639446688,
            "idle_cpu_percent": 0.2,
            "failure_recovery_rate": 1.0,
        }
    proofs = {}
    for gate in ("language", "safety", "licence", "windows", "packaging"):
        payload = {
            "schema": "mumble.local-ai-gate-proof.v1",
            "candidate_id": candidate_id,
            "gate": gate,
            "status": "pass",
            "artifacts": [f"focused-{gate}-evidence"],
        }
        path = proof_root / f"{gate}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        proofs[gate] = {
            "status": "pass",
            "evidence": [
                {
                    "record": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ],
        }
    return {
        "status": "completed",
        "metrics": {"modes": modes},
        "proofs": proofs,
    }


def _self_authored_run_evidence(proof_root, contract, candidate, metrics):
    run_id = "self-authored-run-0001"
    runner = {"id": "unapproved-local-runner", "tool_version": "test-v2"}
    source_commit = "65f27576f093553418540abe58501043971e8b1f"
    corpus_version = contract["corpus"]["version"]
    hardware_id = "windows-z1-extreme-2026-07-26"
    repetitions = {
        mode: values["corpus_runs"] for mode, values in metrics["modes"].items()
    }

    def write_record(name, payload):
        path = proof_root / name
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return {
            "record": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    source_receipt = write_record("source-v2.json", {
        "schema": "mumble.local-ai-source-receipt.v2",
        "candidate_id": candidate["id"],
        "source_commit": source_commit,
        "immutable_tree_sha256": "1" * 64,
    })
    corpus_receipt = write_record("corpus-v2.json", {
        "schema": "mumble.local-ai-corpus-receipt.v2",
        "version": corpus_version,
        "manifest_sha256": "2" * 64,
    })
    hardware_receipt = write_record("hardware-v2.json", {
        "schema": "mumble.local-ai-hardware.v1",
        "id": hardware_id,
    })
    metrics_receipt = write_record("metrics-v2.json", {
        "schema": "mumble.local-ai-metrics-receipt.v2",
        "run_id": run_id,
        "candidate_id": candidate["id"],
        "source_commit": source_commit,
        "corpus_version": corpus_version,
        "hardware_id": hardware_id,
        "runner": runner,
        "repetitions": repetitions,
        "metrics": metrics,
    })
    gate_receipts = {}
    for gate in sorted({
        "accuracy", "latency", "resource", "language", "safety",
        "licence", "windows", "packaging",
    }):
        gate_receipts[gate] = write_record(f"{gate}-v2.json", {
            "schema": "mumble.local-ai-gate-evidence.v2",
            "run_id": run_id,
            "candidate_id": candidate["id"],
            "gate": gate,
            "runner": runner,
            "evidence_kind": "approved_runner",
            "outcome": "pass",
            "observations": [f"self-authored-{gate}"],
        })
    run_receipt = write_record("run-v2.json", {
        "schema": "mumble.local-ai-run-receipt.v2",
        "run_id": run_id,
        "candidate_id": candidate["id"],
        "lane_id": candidate["lane"],
        "source_commit": source_commit,
        "corpus_version": corpus_version,
        "hardware_id": hardware_id,
        "repetitions": repetitions,
        "runner": runner,
        "source_receipt": source_receipt,
        "corpus_receipt": corpus_receipt,
        "hardware_receipt": hardware_receipt,
        "metrics_receipt": metrics_receipt,
        "gate_receipts": gate_receipts,
    })
    return {"status": "completed", "run_receipt": run_receipt}


def test_contract_is_versioned_consent_safe_and_predeclared():
    benchmark = _load_tool()
    contract = benchmark.load_contract(CONTRACT_ROOT)

    assert contract["corpus"]["schema"] == "mumble.local-ai-corpus.v1"
    assert contract["gates"]["schema"] == "mumble.local-ai-gates.v1"
    assert contract["candidates"]["schema"] == "mumble.local-ai-candidates.v1"
    evidence_contract = contract["gates"]["evidence_contract"]
    assert evidence_contract["schema"] == "mumble.local-ai-evidence-contract.v2"
    assert evidence_contract["approved_runner_receipts"] == []
    assert evidence_contract[
        "manual_owner_evidence_can_grant_automated_eligibility"
    ] is False
    assert "Every eligible run receipt hash" in evidence_contract["approval_rule"]

    source_kinds = {
        item["source"]["kind"] for item in contract["corpus"]["items"]
    }
    assert source_kinds <= {"public", "consented", "synthetic"}
    assert "private_recording" not in source_kinds

    candidate_ids = {
        item["id"] for item in contract["candidates"]["candidates"]
    }
    assert {
        "faster-whisper",
        "deterministic-text",
        "moonshine-small-streaming",
        "sherpa-zipformer-streaming-en",
        "parakeet-tdt-0.6b-v3",
    } <= candidate_ids

    required_gates = {
        "accuracy",
        "latency",
        "resource",
        "language",
        "safety",
        "licence",
        "windows",
        "packaging",
    }
    for lane in contract["gates"]["lanes"].values():
        assert set(lane["required_gates"]) == required_gates
    assert contract["gates"]["lanes"]["instruction-text"]["required_modes"] == [
        "prompt",
        "email",
        "reply",
        "classification",
    ]

    required_licence_fields = {
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
    for candidate in contract["candidates"]["candidates"]:
        assert set(candidate["licence"]) == required_licence_fields


def test_speech_metrics_are_content_free_and_lane_appropriate():
    benchmark = _load_tool()
    metrics = benchmark.speech_metrics(
        fixture_id="speech-dictation-001",
        reference="Please send the revised report by Friday.",
        hypothesis="Please send revised report by Friday.",
        audio_duration_ms=4000,
        inference_ms=800,
        first_stable_partial_ms=700,
        stop_to_final_ms=320,
        peak_ram_mb=612,
        peak_vram_mb=0,
        model_size_bytes=145000000,
        idle_cpu_percent=0.2,
        failure_recovery=True,
    )

    assert metrics["fixture_id"] == "speech-dictation-001"
    assert metrics["wer"] == 1 / 7
    assert metrics["real_time_factor"] == 0.2
    assert metrics["failure_recovery"] is True
    encoded = json.dumps(metrics).casefold()
    assert "please send" not in encoded
    assert "reference" not in metrics
    assert "hypothesis" not in metrics

    with pytest.raises(benchmark.ContractError, match="failure_recovery_boolean_required"):
        benchmark.speech_metrics(
            fixture_id="speech-dictation-001",
            reference="one",
            hypothesis="one",
            audio_duration_ms=1000,
            inference_ms=100,
            first_stable_partial_ms=50,
            stop_to_final_ms=100,
            peak_ram_mb=100,
            peak_vram_mb=0,
            model_size_bytes=1000,
            idle_cpu_percent=0,
            failure_recovery="not_run",
        )


def test_preliminary_offline_speech_record_is_content_free_and_non_adopting():
    benchmark = _load_tool()
    record = json.loads(PRELIMINARY_SPEECH_RESULT.read_text(encoding="utf-8"))

    benchmark.validate_preliminary_speech_record(record)

    assert record["evidence_level"] == "single_offline_synthetic_preliminary_only"
    assert record["adoption_gate_status"] == "not_evaluated"
    assert record["decision"] == "retain_baseline_preliminary_measurement_only"
    assert record["metrics"]["wer"] == 0.2
    assert record["metrics"]["first_stable_partial_ms"] is None
    assert record["metrics"]["failure_recovery"] == "not_run"
    assert all(len(item["sha256"]) == 64 for item in record["model_files"])
    encoded = json.dumps(record).casefold()
    assert "hypothesis" not in encoded
    assert "transcript" not in encoded
    assert "audio_path" not in encoded

    run_root = PRELIMINARY_SPEECH_RESULT.parent
    contract = benchmark.load_contract(CONTRACT_ROOT)
    hardware = json.loads((run_root / "hardware.json").read_text(encoding="utf-8"))
    availability_record = json.loads(
        (run_root / "availability.json").read_text(encoding="utf-8")
    )
    ledger = benchmark.build_ledger(
        contract=contract,
        app_dir=ROOT / "Internal" / "app",
        source_parent=availability_record["source_parent"],
        hardware=hardware,
        availability_record=availability_record,
        evidence_root=run_root,
    )
    faster_whisper = next(
        item
        for item in ledger["candidate_results"]
        if item["candidate_id"] == "faster-whisper"
    )
    assert faster_whisper["status"] == "not_run"
    assert faster_whisper["preliminary_result_file"] == PRELIMINARY_SPEECH_RESULT.name


def test_candidate_metrics_require_approved_run_provenance(tmp_path):
    benchmark = _load_tool()
    contract = benchmark.load_contract(CONTRACT_ROOT)
    candidate = next(
        item
        for item in contract["candidates"]["candidates"]
        if item["id"] == "qwen3-0.6b-q8"
    )
    evidence = _passing_instruction_evidence(tmp_path)
    result = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=evidence,
        evidence_root=tmp_path,
    )
    assert result["decision"]["decision"] == "retain_baseline"
    assert set(result["gate_results"].values()) == {"unverified"}
    assert result["provenance"] == {
        "approved": False,
        "evidence_kind": "caller_supplied",
        "reason": "missing_approved_run_receipt",
    }
    assert set(result["gate_evidence"]) == benchmark.REQUIRED_GATE_NAMES

    missing_reply = copy.deepcopy(evidence)
    del missing_reply["metrics"]["modes"]["reply"]
    blocked = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=missing_reply,
        evidence_root=tmp_path,
    )
    assert blocked["decision"]["decision"] == "retain_baseline"
    assert blocked["gate_results"]["accuracy"] == "fail"
    assert "accuracy:fail" in blocked["decision"]["blocking_gates"]

    untraceable = copy.deepcopy(evidence)
    untraceable["proofs"]["licence"]["evidence"][0]["sha256"] = "unknown"
    blocked_proof = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=untraceable,
        evidence_root=tmp_path,
    )
    assert blocked_proof["gate_results"]["licence"] == "fail"

    impossible = copy.deepcopy(evidence)
    impossible_mode = impossible["metrics"]["modes"]["prompt"]
    impossible_mode.update(
        {
            "task_success_rate": 2.0,
            "failure_recovery_rate": 2.0,
            "unsupported_additions": -1,
            "first_token_ms": -1,
            "completion_p95_ms": -1,
            "peak_ram_mb": -1,
            "peak_vram_mb": -1,
            "idle_cpu_percent": -1,
        }
    )
    impossible_result = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=impossible,
        evidence_root=tmp_path,
    )
    assert impossible_result["decision"]["decision"] == "retain_baseline"
    assert {
        impossible_result["gate_results"][gate]
        for gate in ("accuracy", "latency", "resource")
    } == {"fail"}

    content_bearing = copy.deepcopy(evidence)
    content_bearing["metrics"]["transcript_text"] = "private words"
    with pytest.raises(benchmark.ContractError, match="invalid_instruction_metric_schema"):
        benchmark.completed_candidate_result(
            candidate=candidate,
            lane=contract["gates"]["lanes"][candidate["lane"]],
            source_commit="65f27576f093553418540abe58501043971e8b1f",
            hardware_id="windows-z1-extreme-2026-07-26",
            evidence=content_bearing,
            evidence_root=tmp_path,
        )


def test_self_authored_metrics_and_placeholder_proofs_cannot_grant_eligibility(
    tmp_path,
):
    benchmark = _load_tool()
    contract = benchmark.load_contract(CONTRACT_ROOT)
    candidate = next(
        item
        for item in contract["candidates"]["candidates"]
        if item["id"] == "qwen3-0.6b-q8"
    )

    result = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=_passing_instruction_evidence(tmp_path),
        evidence_root=tmp_path,
    )

    assert result["decision"]["decision"] == "retain_baseline"
    assert result["decision"]["blocking_gates"]

    self_authored_run = benchmark.completed_candidate_result(
        candidate=candidate,
        lane=contract["gates"]["lanes"][candidate["lane"]],
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        evidence=_self_authored_run_evidence(
            tmp_path, contract, candidate, result["metrics"]
        ),
        evidence_root=tmp_path,
        corpus_version=contract["corpus"]["version"],
    )
    assert self_authored_run["decision"]["decision"] == "retain_baseline"
    assert set(self_authored_run["gate_results"].values()) == {"unverified"}
    assert self_authored_run["provenance"]["approved"] is False


def test_artifact_cache_is_hash_verified_offline_safe_and_recovers_corruption(tmp_path):
    benchmark = _load_tool()
    payload = b"tiny deterministic model fixture"
    artifact = {
        "id": "fixture-model",
        "filename": "fixture-model.bin",
        "url": "https://example.invalid/fixture-model.bin",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    fetches = []

    def fetcher(url, destination, _max_bytes):
        fetches.append(url)
        destination.write_bytes(payload)

    missing = benchmark.prepare_artifact(
        cache_root=tmp_path,
        artifact=artifact,
        offline=True,
        fetcher=fetcher,
        baseline_id="faster-whisper",
    )
    assert missing["status"] == "missing"
    assert missing["fallback"] == "faster-whisper"
    assert fetches == []

    cached = tmp_path / artifact["filename"]
    cached.write_bytes(b"corrupt")
    recovered = benchmark.prepare_artifact(
        cache_root=tmp_path,
        artifact=artifact,
        offline=False,
        fetcher=fetcher,
        baseline_id="faster-whisper",
    )
    assert recovered["status"] == "ready"
    assert recovered["recovered_corrupt_cache"] is True
    assert cached.read_bytes() == payload
    assert fetches == [artifact["url"]]

    def forbidden_fetcher(_url, _destination, _max_bytes):
        raise AssertionError("offline startup attempted a download")

    offline_ready = benchmark.prepare_artifact(
        cache_root=tmp_path,
        artifact=artifact,
        offline=True,
        fetcher=forbidden_fetcher,
        baseline_id="faster-whisper",
    )
    assert offline_ready["status"] == "ready"
    assert offline_ready["recovered_corrupt_cache"] is False

    bad_root = tmp_path / "bad-download"

    def corrupt_fetcher(_url, destination, _max_bytes):
        destination.write_bytes(b"wrong bytes")

    with pytest.raises(benchmark.ContractError, match="artifact_verification_failed"):
        benchmark.prepare_artifact(
            cache_root=bad_root,
            artifact=artifact,
            offline=False,
            fetcher=corrupt_fetcher,
            baseline_id="faster-whisper",
        )
    assert not (bad_root / artifact["filename"]).exists()
    assert not (bad_root / (artifact["filename"] + ".part")).exists()


def test_network_fetch_aborts_oversize_and_rejects_https_downgrade(tmp_path, monkeypatch):
    benchmark = _load_tool()

    class FakeResponse(io.BytesIO):
        def __init__(self, payload, final_url):
            super().__init__(payload)
            self.final_url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def geturl(self):
            return self.final_url

    artifact = {
        "id": "bounded",
        "filename": "bounded.bin",
        "url": "https://example.invalid/bounded.bin",
        "sha256": hashlib.sha256(b"abc").hexdigest(),
        "size_bytes": 3,
    }
    monkeypatch.setattr(
        benchmark.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b"abcd", artifact["url"]),
    )
    with pytest.raises(benchmark.ContractError, match="artifact_download_exceeds_expected_size"):
        benchmark.prepare_artifact(
            cache_root=tmp_path / "oversize",
            artifact=artifact,
            offline=False,
            baseline_id="faster-whisper",
        )
    assert not (tmp_path / "oversize" / "bounded.bin.part").exists()

    monkeypatch.setattr(
        benchmark.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b"abc", "http://example.invalid/bounded.bin"),
    )
    with pytest.raises(benchmark.ContractError, match="artifact_redirect_must_remain_https"):
        benchmark.prepare_artifact(
            cache_root=tmp_path / "redirect",
            artifact=artifact,
            offline=False,
            baseline_id="faster-whisper",
        )
    assert not (tmp_path / "redirect" / "bounded.bin.part").exists()


def test_deterministic_baseline_runs_prompt_email_reply_and_classification_without_content():
    benchmark = _load_tool()
    contract = benchmark.load_contract(CONTRACT_ROOT)
    result = benchmark.run_deterministic_baseline(
        contract=contract,
        app_dir=ROOT / "Internal" / "app",
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
    )

    assert result["schema"] == "mumble.local-ai-benchmark-result.v1"
    assert result["candidate_id"] == "deterministic-text"
    assert result["status"] == "completed"
    assert result["evidence_level"] == "harness_smoke_only"
    assert result["adoption_gate_status"] == "not_evaluated"
    assert set(result["lanes"]) == {"prompt", "email", "reply", "classification"}
    assert all(lane["task_success_rate"] == 1.0 for lane in result["lanes"].values())
    assert all(
        lane["failure_recovery"] == "not_run"
        for lane in result["lanes"].values()
    )
    assert result["lanes"]["classification"]["false_action_count"] == 0
    assert result["lanes"]["classification"]["execution_authority"] is False

    false_action_contract = copy.deepcopy(contract)
    false_action_fixture = next(
        item
        for item in false_action_contract["corpus"]["items"]
        if item["id"] == "classification-not-command-001"
    )
    false_action_fixture["input"] = "open the deck"
    false_action = benchmark.run_deterministic_baseline(
        contract=false_action_contract,
        app_dir=ROOT / "Internal" / "app",
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
    )
    assert false_action["lanes"]["classification"]["false_action_count"] == 1

    encoded = json.dumps(result).casefold()
    assert "archive my photos" not in encoded
    assert "draft is ready" not in encoded
    assert "the deck outside" not in encoded


def test_not_run_candidate_retains_declared_baseline_with_exact_reasons():
    benchmark = _load_tool()
    result = benchmark.not_run_result(
        candidate_id="parakeet-tdt-0.6b-v3",
        baseline_id="faster-whisper",
        source_commit="65f27576f093553418540abe58501043971e8b1f",
        hardware_id="windows-z1-extreme-2026-07-26",
        reasons=[
            "speech_corpus_not_materialized",
            "model_weights_not_present",
            "nemo_runtime_not_present",
            "installed_package_gate_not_authorized",
        ],
        gate_results={
            "accuracy": "not_run",
            "latency": "not_run",
            "resource": "not_run",
            "language": "not_run",
            "safety": "not_run",
            "licence": "blocked",
            "windows": "not_run",
            "packaging": "not_run",
        },
    )
    assert result["status"] == "not_run"
    assert result["decision"]["decision"] == "retain_baseline"
    assert result["decision"]["blocking_gates"] == [
        "accuracy:not_run",
        "language:not_run",
        "latency:not_run",
        "licence:blocked",
        "packaging:not_run",
        "resource:not_run",
        "safety:not_run",
        "windows:not_run",
    ]
    assert result["reasons"] == [
        "speech_corpus_not_materialized",
        "model_weights_not_present",
        "nemo_runtime_not_present",
        "installed_package_gate_not_authorized",
    ]


def test_ledger_accounts_for_every_candidate_and_keeps_unrun_lanes_on_baseline(tmp_path):
    benchmark = _load_tool()
    contract = benchmark.load_contract(CONTRACT_ROOT)
    availability = {}
    for candidate in contract["candidates"]["candidates"]:
        if candidate["id"] == "deterministic-text":
            continue
        availability[candidate["id"]] = {
            "reasons": ["test_artifact_unavailable"],
            "gate_results": {
                name: ("blocked" if name == "licence" else "not_run")
                for name in benchmark.REQUIRED_GATE_NAMES
            },
        }

    availability_record = {
        "schema": "mumble.local-ai-availability.v1",
        "source_parent": "65f27576f093553418540abe58501043971e8b1f",
        "hardware_id": "windows-z1-extreme-2026-07-26",
        "candidate_availability": availability,
    }
    ledger = benchmark.build_ledger(
        contract=contract,
        app_dir=ROOT / "Internal" / "app",
        source_parent="65f27576f093553418540abe58501043971e8b1f",
        hardware={
            "schema": "mumble.local-ai-hardware.v1",
            "id": "windows-z1-extreme-2026-07-26",
        },
        availability_record=availability_record,
        evidence_root=tmp_path,
    )
    candidate_ids = {
        result["candidate_id"] for result in ledger["candidate_results"]
    }
    assert candidate_ids == {
        candidate["id"] for candidate in contract["candidates"]["candidates"]
    }
    assert ledger["adoption"] == "no_candidate_adopted"
    assert ledger["retained_baselines"] == ["deterministic-text", "faster-whisper"]
    assert all(
        result.get("decision", {}).get("decision") != "eligible_for_integration_review"
        for result in ledger["candidate_results"]
        if result["candidate_id"] != "deterministic-text"
    )

    mismatched = dict(availability_record)
    mismatched["hardware_id"] = "different-host"
    with pytest.raises(benchmark.ContractError, match="availability_hardware_mismatch"):
        benchmark.build_ledger(
            contract=contract,
            app_dir=ROOT / "Internal" / "app",
            source_parent="65f27576f093553418540abe58501043971e8b1f",
            hardware={
                "schema": "mumble.local-ai-hardware.v1",
                "id": "windows-z1-extreme-2026-07-26",
            },
            availability_record=mismatched,
            evidence_root=tmp_path,
        )

    completed_availability = copy.deepcopy(availability_record)
    completed_availability["candidate_availability"]["qwen3-0.6b-q8"] = (
        _passing_instruction_evidence(tmp_path)
    )
    completed_ledger = benchmark.build_ledger(
        contract=contract,
        app_dir=ROOT / "Internal" / "app",
        source_parent="65f27576f093553418540abe58501043971e8b1f",
        hardware={
            "schema": "mumble.local-ai-hardware.v1",
            "id": "windows-z1-extreme-2026-07-26",
        },
        availability_record=completed_availability,
        evidence_root=tmp_path,
    )
    assert completed_ledger["eligible_candidates"] == []
    assert completed_ledger["adoption"] == "no_candidate_adopted"


def test_cli_exposes_reproducible_cached_only_sapi_baseline():
    benchmark = _load_tool()
    assert callable(benchmark.run_preliminary_faster_whisper)
    with pytest.raises(SystemExit) as exited:
        benchmark.main(["preliminary-faster-whisper", "--help"])
    assert exited.value.code == 0


def test_cached_baseline_identity_is_bound_to_exact_manifest(tmp_path):
    benchmark = _load_tool()
    revision = "d1d751a5f8271d482d14ca55d9e2deeebbae577f"
    model_dir = tmp_path / revision
    model_dir.mkdir()
    for name, payload in {
        "config.json": b"config",
        "model.bin": b"model",
        "tokenizer.json": b"tokenizer",
    }.items():
        (model_dir / name).write_bytes(payload)
    expected = {
        "schema": "mumble.local-ai-preliminary-speech.v1",
        "candidate_id": "faster-whisper",
        "model": "Systran/faster-whisper-small.en",
        "snapshot_revision": revision,
        "model_files": [
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(model_dir.iterdir())
        ],
    }
    identity = benchmark.validate_cached_faster_whisper_snapshot(
        model_dir=model_dir,
        expected_snapshot=expected,
    )
    assert identity["model"] == "Systran/faster-whisper-small.en"

    wrong = copy.deepcopy(expected)
    wrong["model"] = "Systran/faster-whisper-base.en"
    with pytest.raises(benchmark.ContractError, match="unexpected_cached_model_identity"):
        benchmark.validate_cached_faster_whisper_snapshot(
            model_dir=model_dir,
            expected_snapshot=wrong,
        )
