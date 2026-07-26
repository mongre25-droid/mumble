"""Stage A behaviour tests for durable logical dictation sessions."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from dictation_session import (
    DEFAULT_SEGMENT_SECONDS,
    DeterministicFaultInjector,
    DictationSessionError,
    DurableDictationSession,
    FinalizationOwnershipError,
    InjectedCrash,
    STATE_TRANSITIONS,
    default_recovery_root,
    default_segment_max_samples,
)


SESSION_ID = "0123456789abcdef0123456789abcdef"
OWNER_ID = "11111111111111111111111111111111"
OPERATION_ID = "22222222222222222222222222222222"
EXPECTED_TRANSITIONS = (
    "session_created",
    "segment_reserved",
    "segment_sealed",
    "finalization_claimed",
    "history_committed",
    "insertion_claimed",
    "finalization_completed",
)


def _manifest(root):
    path = root / SESSION_ID / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage_a_contract_names_every_crash_transition_and_private_location():
    import branding

    assert DEFAULT_SEGMENT_SECONDS == 30
    assert STATE_TRANSITIONS == EXPECTED_TRANSITIONS
    assert default_recovery_root() == (
        Path(branding.DATA_DIR) / "dictation_recovery"
    )
    assert default_segment_max_samples(16_000) == 480_000
    with pytest.raises(ValueError, match="invalid_sample_rate"):
        default_segment_max_samples(0)


def test_session_seals_one_contiguous_immutable_pcm16_segment(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"

    segment = session.append_pcm16(pcm)

    manifest = _manifest(tmp_path)
    assert manifest["schema"] == "mumble.dictation-session.v1"
    assert manifest["session_id"] == SESSION_ID
    assert manifest["state"] == "capturing"
    assert manifest["audio"] == {
        "sample_rate": 16_000,
        "channels": 1,
        "encoding": "pcm_s16le",
    }
    assert manifest["next_sample"] == 4
    assert manifest["segments"] == [{
        "number": 0,
        "state": "sealed",
        "sample_start": 0,
        "sample_end": 4,
        "sample_count": 4,
        "byte_count": 8,
        "sha256": hashlib.sha256(pcm).hexdigest(),
        "filename": "segment-00000000.pcm",
    }]
    assert segment == manifest["segments"][0]
    assert (tmp_path / SESSION_ID / segment["filename"]).read_bytes() == pcm
    assert not list((tmp_path / SESSION_ID).glob("*.tmp"))


def test_recovery_preserves_reserved_range_and_blocks_all_finalization(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )
    pcm = b"\x01\x00\x02\x00"

    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        session.append_pcm16(pcm)

    reserved = _manifest(tmp_path)
    assert reserved["next_sample"] == 2
    assert reserved["segments"][0]["state"] == "writing"

    session.fault_injector = None
    with pytest.raises(DictationSessionError, match="segment_unsealed"):
        session.claim_finalization(OWNER_ID, OPERATION_ID)

    for _ in range(3):
        recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
        unresolved = recovered.read_manifest()
        assert unresolved["segments"] == reserved["segments"]
        assert unresolved["next_sample"] == 2

    with pytest.raises(DictationSessionError, match="segment_unresolved"):
        recovered.append_pcm16(b"\x03\x00")

    advances = (
        lambda: recovered.claim_finalization(OWNER_ID, OPERATION_ID),
        lambda: recovered.mark_history_committed(OWNER_ID, OPERATION_ID),
        lambda: recovered.claim_final_insertion(OWNER_ID, OPERATION_ID),
        lambda: recovered.complete_finalization(
            OWNER_ID, OPERATION_ID, insertion_outcome="confirmed"
        ),
    )
    for advance in advances:
        with pytest.raises(DictationSessionError):
            advance()

    assert recovered.read_manifest() == reserved


def test_finalization_rejects_reserved_unverified_audio(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )

    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        session.append_pcm16(b"\x01\x00\x02\x00")

    session.fault_injector = None
    with pytest.raises(DictationSessionError, match="segment_unsealed"):
        session.claim_finalization(OWNER_ID, OPERATION_ID)
    assert _manifest(tmp_path)["state"] == "capturing"


def test_exact_reconciliation_seals_original_range_once_and_can_finalize(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    pcm = b"\x01\x00\x02\x00"
    session.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )
    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        session.append_pcm16(pcm)

    recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
    repaired = recovered.reconcile_unresolved_pcm16(pcm, channels=1)

    assert repaired["number"] == 0
    assert (repaired["sample_start"], repaired["sample_end"]) == (0, 2)
    assert repaired["state"] == "sealed"
    assert recovered.verify()["next_sample"] == 2
    with pytest.raises(DictationSessionError, match="no_unresolved_segment"):
        recovered.reconcile_unresolved_pcm16(pcm, channels=1)

    recovered.claim_finalization(OWNER_ID, OPERATION_ID)
    assert recovered.mark_history_committed(OWNER_ID, OPERATION_ID) is True
    assert recovered.claim_final_insertion(OWNER_ID, OPERATION_ID) is True
    assert recovered.claim_final_insertion(OWNER_ID, OPERATION_ID) is False
    completed = recovered.complete_finalization(
        OWNER_ID, OPERATION_ID, insertion_outcome="confirmed"
    )
    assert completed["state"] == "complete"
    assert recovered.verify()["next_sample"] == 2


@pytest.mark.parametrize(
    ("payload", "channels", "error"),
    (
        (b"\x09\x00\x02\x00", 1, "reconciliation_checksum_mismatch"),
        (b"\x01\x00", 1, "reconciliation_length_mismatch"),
        (b"\x01\x00\x02\x00", 2, "reconciliation_channels_mismatch"),
    ),
)
def test_reconciliation_rejects_mismatch_without_mutating_reservation(
        tmp_path, payload, channels, error):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )
    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        session.append_pcm16(b"\x01\x00\x02\x00")
    recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
    manifest_before = recovered.manifest_path.read_bytes()
    files_before = sorted(path.name for path in recovered.path.iterdir())

    with pytest.raises(DictationSessionError, match=error):
        recovered.reconcile_unresolved_pcm16(payload, channels=channels)

    assert recovered.manifest_path.read_bytes() == manifest_before
    assert sorted(path.name for path in recovered.path.iterdir()) == files_before
    assert recovered.read_manifest()["next_sample"] == 2
    assert recovered.read_manifest()["segments"][0]["state"] == "writing"


@pytest.mark.parametrize("point", ("before:segment_sealed", "after:segment_sealed"))
def test_exact_reconciliation_is_idempotent_across_sealing_crashes(
        tmp_path, point):
    pcm = b"\x01\x00\x02\x00"
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )
    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        session.append_pcm16(pcm)
    recovered = DurableDictationSession.open(
        tmp_path,
        SESSION_ID,
        fault_injector=DeterministicFaultInjector(point),
    )

    with pytest.raises(InjectedCrash, match=point):
        recovered.reconcile_unresolved_pcm16(pcm, channels=1)

    reopened = DurableDictationSession.open(tmp_path, SESSION_ID)
    verified = reopened.verify()
    assert verified["next_sample"] == 2
    assert len(verified["segments"]) == 1
    assert verified["segments"][0]["state"] == "sealed"
    with pytest.raises(DictationSessionError, match="no_unresolved_segment"):
        reopened.reconcile_unresolved_pcm16(pcm, channels=1)
    assert sorted(path.name for path in reopened.path.glob("*.pcm")) == [
        "segment-00000000.pcm"
    ]


def test_one_finalization_owner_and_operation_prevent_duplicate_insertion(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )

    first = session.claim_finalization(OWNER_ID, OPERATION_ID)
    repeated = session.claim_finalization(OWNER_ID, OPERATION_ID)

    assert first == repeated
    assert first["owner_id"] == OWNER_ID
    assert first["operation_id"] == OPERATION_ID
    assert session.mark_history_committed(OWNER_ID, OPERATION_ID) is True
    assert session.mark_history_committed(OWNER_ID, OPERATION_ID) is False
    assert session.claim_final_insertion(OWNER_ID, OPERATION_ID) is True
    assert session.claim_final_insertion(OWNER_ID, OPERATION_ID) is False
    completed = session.complete_finalization(
        OWNER_ID, OPERATION_ID, insertion_outcome="confirmed"
    )
    assert completed["state"] == "complete"
    assert completed["history_record_id"] == SESSION_ID
    assert completed["insertion_state"] == "requested"
    assert completed["insertion_outcome"] == "confirmed"
    assert session.read_manifest()["state"] == "complete"

    with pytest.raises(FinalizationOwnershipError):
        session.claim_finalization(
            "33333333333333333333333333333333", OPERATION_ID
        )
    with pytest.raises(FinalizationOwnershipError):
        session.claim_final_insertion(
            OWNER_ID, "44444444444444444444444444444444"
        )


def test_verification_rejects_checksum_failure_without_rewriting_evidence(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    segment = session.append_pcm16(b"\x01\x00\x02\x00")
    manifest_before = session.manifest_path.read_bytes()
    (session.path / segment["filename"]).write_bytes(b"\x09\x00\x02\x00")

    with pytest.raises(DictationSessionError, match="segment_checksum_mismatch"):
        session.verify()

    assert session.manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize("transition", STATE_TRANSITIONS)
@pytest.mark.parametrize("side", ("before", "after"))
def test_every_state_transition_recovers_idempotently(tmp_path, transition, side):
    point = f"{side}:{transition}"
    injector = DeterministicFaultInjector(point)

    if transition == "session_created":
        with pytest.raises(InjectedCrash, match=point):
            DurableDictationSession.create(
                tmp_path,
                session_id=SESSION_ID,
                sample_rate=16_000,
                channels=1,
                segment_max_samples=4,
                fault_injector=injector,
            )
        if side == "before":
            session = DurableDictationSession.create(
                tmp_path,
                session_id=SESSION_ID,
                sample_rate=16_000,
                channels=1,
                segment_max_samples=4,
            )
        else:
            session = DurableDictationSession.open(tmp_path, SESSION_ID)
        assert session.verify()["state"] == "capturing"
        return

    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.fault_injector = injector

    if transition in ("segment_reserved", "segment_sealed"):
        with pytest.raises(InjectedCrash, match=point):
            session.append_pcm16(b"\x01\x00\x02\x00")
        recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
        recovered_manifest = recovered.read_manifest()
        if not recovered_manifest["segments"]:
            recovered.append_pcm16(b"\x01\x00\x02\x00")
        elif recovered_manifest["segments"][-1]["state"] == "writing":
            recovered.reconcile_unresolved_pcm16(
                b"\x01\x00\x02\x00", channels=1
            )
        assert recovered.verify()["next_sample"] == 2
        return

    session.fault_injector = None
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    if transition == "history_committed":
        session.fault_injector = injector
        with pytest.raises(InjectedCrash, match=point):
            session.mark_history_committed(OWNER_ID, OPERATION_ID)
        recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
        recovered.mark_history_committed(OWNER_ID, OPERATION_ID)
        assert recovered.read_manifest()["finalization"]["history_state"] == "committed"
        return

    if transition == "finalization_claimed":
        # Recreate the pre-transition state because the common setup claimed it.
        other_root = tmp_path / "claim"
        session = DurableDictationSession.create(
            other_root,
            session_id=SESSION_ID,
            sample_rate=16_000,
            channels=1,
            segment_max_samples=4,
        )
        session.fault_injector = injector
        with pytest.raises(InjectedCrash, match=point):
            session.claim_finalization(OWNER_ID, OPERATION_ID)
        recovered = DurableDictationSession.open(other_root, SESSION_ID)
        recovered.claim_finalization(OWNER_ID, OPERATION_ID)
        assert recovered.read_manifest()["finalization"]["owner_id"] == OWNER_ID
        return

    session.mark_history_committed(OWNER_ID, OPERATION_ID)
    if transition == "insertion_claimed":
        session.fault_injector = injector
        with pytest.raises(InjectedCrash, match=point):
            session.claim_final_insertion(OWNER_ID, OPERATION_ID)
        recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
        recovered.claim_final_insertion(OWNER_ID, OPERATION_ID)
        assert recovered.read_manifest()["finalization"]["insertion_state"] == "requested"
        return

    session.claim_final_insertion(OWNER_ID, OPERATION_ID)
    assert transition == "finalization_completed"
    session.fault_injector = injector
    with pytest.raises(InjectedCrash, match=point):
        session.complete_finalization(
            OWNER_ID, OPERATION_ID, insertion_outcome="confirmed"
        )
    recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
    completed = recovered.complete_finalization(
        OWNER_ID, OPERATION_ID, insertion_outcome="confirmed"
    )
    assert completed["state"] == "complete"


def test_concurrent_same_operation_claims_one_final_insertion(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    session.mark_history_committed(OWNER_ID, OPERATION_ID)
    first = DurableDictationSession.open(tmp_path, SESSION_ID)
    second = DurableDictationSession.open(tmp_path, SESSION_ID)
    barrier = threading.Barrier(2)

    def synchronize(point):
        if point == "before:insertion_claimed":
            barrier.wait(timeout=2.0)

    first.fault_injector = synchronize
    second.fault_injector = synchronize
    results = []
    errors = []

    def claim(handle):
        try:
            results.append(handle.claim_final_insertion(OWNER_ID, OPERATION_ID))
        except Exception as error:  # retained for an exact race assertion
            errors.append(error)

    threads = [
        threading.Thread(target=claim, args=(first,)),
        threading.Thread(target=claim, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert errors == []
    assert sorted(results) == [False, True]
    assert _manifest(tmp_path)["finalization"]["insertion_state"] == "requested"


def test_concurrent_finalizers_leave_exactly_one_owner(tmp_path):
    DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    first = DurableDictationSession.open(tmp_path, SESSION_ID)
    second = DurableDictationSession.open(tmp_path, SESSION_ID)
    barrier = threading.Barrier(2)

    def synchronize(point):
        if point == "before:finalization_claimed":
            barrier.wait(timeout=2.0)

    first.fault_injector = synchronize
    second.fault_injector = synchronize
    claims = []
    errors = []
    identities = (
        (OWNER_ID, OPERATION_ID),
        (
            "33333333333333333333333333333333",
            "44444444444444444444444444444444",
        ),
    )

    def claim(handle, owner_id, operation_id):
        try:
            claims.append(handle.claim_finalization(owner_id, operation_id))
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=claim, args=(first, *identities[0])),
        threading.Thread(target=claim, args=(second, *identities[1])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert len(claims) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], FinalizationOwnershipError)
    durable = _manifest(tmp_path)["finalization"]
    assert (durable["owner_id"], durable["operation_id"]) in identities


def test_open_waits_for_an_active_append_before_recovery(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    reservation_published = threading.Event()
    release_writer = threading.Event()
    open_started = threading.Event()
    open_finished = threading.Event()
    errors = []

    def pause_writer(point):
        if point == "after:segment_reserved":
            reservation_published.set()
            if not release_writer.wait(timeout=2.0):
                raise AssertionError("test did not release active writer")

    session.fault_injector = pause_writer

    def append():
        try:
            session.append_pcm16(b"\x01\x00\x02\x00")
        except Exception as error:
            errors.append(error)

    def reopen():
        open_started.set()
        try:
            DurableDictationSession.open(tmp_path, SESSION_ID)
        except Exception as error:
            errors.append(error)
        finally:
            open_finished.set()

    writer = threading.Thread(target=append)
    writer.start()
    assert reservation_published.wait(timeout=2.0)
    opener = threading.Thread(target=reopen)
    opener.start()
    assert open_started.wait(timeout=2.0)

    assert not open_finished.wait(timeout=0.2)
    release_writer.set()
    writer.join(timeout=3.0)
    opener.join(timeout=3.0)

    assert not writer.is_alive()
    assert not opener.is_alive()
    assert errors == []
    assert DurableDictationSession.open(
        tmp_path, SESSION_ID
    ).verify()["next_sample"] == 2
    assert sorted(path.name for path in session.path.glob("*.pcm")) == [
        "segment-00000000.pcm"
    ]


def test_open_waits_for_an_active_append_across_processes(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    reservation_published = threading.Event()
    release_writer = threading.Event()
    writer_errors = []

    def pause_writer(point):
        if point == "after:segment_reserved":
            reservation_published.set()
            if not release_writer.wait(timeout=3.0):
                raise AssertionError("test did not release active writer")

    session.fault_injector = pause_writer

    def append():
        try:
            session.append_pcm16(b"\x01\x00\x02\x00")
        except Exception as error:
            writer_errors.append(error)

    writer = threading.Thread(target=append)
    writer.start()
    assert reservation_published.wait(timeout=2.0)
    app_path = str(Path(__file__).resolve().parent)
    script = (
        "import sys; "
        f"sys.path.insert(0, {app_path!r}); "
        "from dictation_session import DurableDictationSession; "
        f"DurableDictationSession.open({str(tmp_path)!r}, {SESSION_ID!r}); "
        "print('opened')"
    )
    opener = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            opener.wait(timeout=0.2)
        release_writer.set()
        stdout, stderr = opener.communicate(timeout=3.0)
    finally:
        release_writer.set()
        if opener.poll() is None:
            opener.kill()
            opener.communicate(timeout=3.0)
    writer.join(timeout=3.0)

    assert not writer.is_alive()
    assert writer_errors == []
    assert opener.returncode == 0, stderr
    assert stdout.strip() == "opened"
    assert session.verify()["next_sample"] == 2


def test_recovery_rejects_a_segment_path_outside_its_session(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    pcm = b"\x01\x00\x02\x00"
    segment = session.append_pcm16(pcm)
    (session.path / segment["filename"]).unlink()
    (tmp_path / "outside.pcm").write_bytes(pcm)
    manifest = session.read_manifest()
    manifest["segments"][0]["filename"] = "../outside.pcm"
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DictationSessionError, match="segment_filename_invalid"):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_segment_bound_and_multiple_ranges_are_exact(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    first = session.append_pcm16(b"\x01\x00\x02\x00")
    second = session.append_pcm16(b"\x03\x00")

    assert (first["sample_start"], first["sample_end"]) == (0, 2)
    assert (second["sample_start"], second["sample_end"]) == (2, 3)
    assert session.verify()["next_sample"] == 3
    with pytest.raises(ValueError, match="segment_too_large"):
        session.append_pcm16(b"\x04\x00\x05\x00\x06\x00")


def test_recovery_discards_only_unpublished_temporary_bytes(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.append_pcm16(b"\x01\x00\x02\x00")
    (session.path / "segment-00000001.pcm.tmp").write_bytes(b"partial")
    (session.path / "manifest.json.tmp").write_bytes(b'{"partial":')

    recovered = DurableDictationSession.open(tmp_path, SESSION_ID)

    assert recovered.verify()["next_sample"] == 2
    assert not list(session.path.glob("*.tmp"))


def test_restart_discovers_only_valid_private_session_directories(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    session.append_pcm16(b"\x01\x00\x02\x00")
    (tmp_path / "notes").mkdir()
    (tmp_path / "not-a-session.txt").write_text("ignore", encoding="utf-8")

    discovered = DurableDictationSession.discover(tmp_path)

    assert [item.session_id for item in discovered] == [SESSION_ID]
    assert discovered[0].verify()["next_sample"] == 2


def test_discovery_reports_unresolved_session_without_deleting_recovery_bytes(
        tmp_path):
    healthy_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    healthy = DurableDictationSession.create(
        tmp_path,
        session_id=healthy_id,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    healthy.append_pcm16(b"\x01\x00")
    unresolved = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    unresolved.fault_injector = DeterministicFaultInjector(
        "after:segment_reserved"
    )
    with pytest.raises(InjectedCrash, match="after:segment_reserved"):
        unresolved.append_pcm16(b"\x02\x00\x03\x00")
    partial = unresolved.path / "segment-00000000.pcm.tmp"
    partial.write_bytes(b"\x02\x00")
    reserved = _manifest(tmp_path)
    issues = []

    discovered = DurableDictationSession.discover(
        tmp_path, on_error=issues.append
    )

    assert [item.session_id for item in discovered] == [healthy_id]
    assert issues == [{
        "session_id": SESSION_ID,
        "error": "segment_unresolved",
    }]
    assert _manifest(tmp_path) == reserved
    assert partial.read_bytes() == b"\x02\x00"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("encoding", "float32", "manifest_audio_invalid"),
        ("channels", 0, "manifest_audio_invalid"),
        ("sample_rate", -1, "manifest_audio_invalid"),
    ),
)
def test_recovery_rejects_untrusted_audio_schema(tmp_path, field, value, error):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=4,
    )
    manifest = session.read_manifest()
    manifest["audio"][field] = value
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DictationSessionError, match=error):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_manifest_rejects_a_segment_larger_than_its_declared_bound(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    session.append_pcm16(b"\x01\x00\x02\x00")
    manifest = session.read_manifest()
    manifest["segment_max_samples"] = 1
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DictationSessionError, match="segment_bound_exceeded"):
        DurableDictationSession.open(tmp_path, SESSION_ID)


@pytest.mark.parametrize(
    ("case", "error"),
    (
        ("pcm_length", "segment_pcm_length_invalid"),
        ("unknown_transcript", "manifest_fields_invalid"),
        ("unknown_clipboard", "manifest_finalization_invalid"),
        ("contradictory_state", "manifest_state_combination_invalid"),
        ("bad_checksum_shape", "segment_checksum_invalid"),
        ("bad_segment_number", "segment_ranges_not_contiguous"),
    ),
)
def test_manifest_schema_fails_closed_for_malformed_or_content_fields(
        tmp_path, case, error):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    session.append_pcm16(b"\x01\x00\x02\x00")
    manifest = session.read_manifest()
    if case == "pcm_length":
        manifest["segments"][0]["byte_count"] = 2
    elif case == "unknown_transcript":
        manifest["transcript"] = "private words"
    elif case == "unknown_clipboard":
        manifest["finalization"]["clipboard_text"] = "private words"
    elif case == "contradictory_state":
        manifest["state"] = "finalizing"
    elif case == "bad_checksum_shape":
        manifest["segments"][0]["sha256"] = "not-a-sha256"
    else:
        manifest["segments"][0]["number"] = 9
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DictationSessionError, match=error):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_manifest_enforces_pcm_bytes_for_multiple_channels(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=2,
        segment_max_samples=2,
    )
    session.append_pcm16(b"\x01\x00\x02\x00\x03\x00\x04\x00")
    manifest = session.read_manifest()
    manifest["segments"][0]["byte_count"] = 4
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
            DictationSessionError, match="segment_pcm_length_invalid"):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_manifest_rejects_invalid_finalization_ids_and_combinations(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    manifest = session.read_manifest()
    manifest["finalization"]["owner_id"] = "owner"
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(
            DictationSessionError, match="manifest_finalization_invalid"):
        DurableDictationSession.open(tmp_path, SESSION_ID)

    manifest["finalization"]["owner_id"] = OWNER_ID
    manifest["finalization"]["insertion_state"] = "requested"
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(
            DictationSessionError, match="manifest_state_combination_invalid"):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_resolved_segment_path_cannot_escape_through_a_symbolic_link(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    segment = session.append_pcm16(b"\x01\x00\x02\x00")
    target = session.path / segment["filename"]
    outside = tmp_path / "outside.pcm"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError):
        manifest = session.read_manifest()
        manifest["segments"][0]["filename"] = "../outside.pcm"
        session.manifest_path.write_text(
            json.dumps(manifest), encoding="utf-8")
        expected_error = "segment_filename_invalid"
    else:
        expected_error = "segment_path_outside_session"

    with pytest.raises(DictationSessionError, match=expected_error):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_finalization_advances_only_while_every_segment_stays_verified(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    segment = session.append_pcm16(b"\x01\x00\x02\x00")
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    (session.path / segment["filename"]).write_bytes(b"\x09\x00\x02\x00")

    with pytest.raises(
            DictationSessionError, match="segment_checksum_mismatch"):
        session.mark_history_committed(OWNER_ID, OPERATION_ID)


def test_finalization_completion_rechecks_audio_and_insertion_claim(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    segment = session.append_pcm16(b"\x01\x00\x02\x00")
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    session.mark_history_committed(OWNER_ID, OPERATION_ID)
    with pytest.raises(DictationSessionError, match="insertion_not_claimed"):
        session.complete_finalization(
            OWNER_ID, OPERATION_ID, insertion_outcome="confirmed")
    session.claim_final_insertion(OWNER_ID, OPERATION_ID)
    (session.path / segment["filename"]).write_bytes(b"\x09\x00\x02\x00")

    with pytest.raises(
            DictationSessionError, match="segment_checksum_mismatch"):
        session.complete_finalization(
            OWNER_ID, OPERATION_ID, insertion_outcome="confirmed")


def test_completed_session_accounts_for_every_published_segment(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    session.append_pcm16(b"\x01\x00\x02\x00")
    session.claim_finalization(OWNER_ID, OPERATION_ID)
    session.mark_history_committed(OWNER_ID, OPERATION_ID)
    session.claim_final_insertion(OWNER_ID, OPERATION_ID)
    session.complete_finalization(
        OWNER_ID, OPERATION_ID, insertion_outcome="confirmed")
    manifest = session.read_manifest()
    manifest["segments"] = []
    manifest["next_sample"] = 0
    session.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DictationSessionError, match="unexpected_segment_file"):
        DurableDictationSession.open(tmp_path, SESSION_ID)


def test_interrupted_create_is_idempotently_recreated(tmp_path):
    injector = DeterministicFaultInjector("after:session_directory_created")
    with pytest.raises(
            InjectedCrash, match="after:session_directory_created"):
        DurableDictationSession.create(
            tmp_path,
            session_id=SESSION_ID,
            sample_rate=16_000,
            channels=1,
            segment_max_samples=2,
            fault_injector=injector,
        )
    assert (tmp_path / SESSION_ID).is_dir()
    assert not (tmp_path / SESSION_ID / "manifest.json").exists()

    recreated = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    repeated = DurableDictationSession.create(
        tmp_path,
        session_id=SESSION_ID,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    assert recreated.verify()["next_sample"] == 0
    assert repeated.verify()["session_id"] == SESSION_ID


def test_discovery_reports_incomplete_session_and_returns_healthy_ones(tmp_path):
    healthy_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    healthy = DurableDictationSession.create(
        tmp_path,
        session_id=healthy_id,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=2,
    )
    healthy.append_pcm16(b"\x01\x00")
    incomplete = tmp_path / SESSION_ID
    incomplete.mkdir()
    recoverable = incomplete / "segment-00000000.pcm"
    recoverable.write_bytes(b"\x02\x00")
    unpublished = incomplete / "segment-00000001.pcm.tmp"
    unpublished.write_bytes(b"partial audio")
    issues = []

    discovered = DurableDictationSession.discover(
        tmp_path, on_error=issues.append)

    assert [item.session_id for item in discovered] == [healthy_id]
    assert issues == [{
        "session_id": SESSION_ID,
        "error": "manifest_unreadable",
    }]
    assert recoverable.read_bytes() == b"\x02\x00"
    assert unpublished.read_bytes() == b"partial audio"
    with pytest.raises(
            DictationSessionError,
            match="incomplete_session_contains_recoverable_data"):
        DurableDictationSession.create(
            tmp_path,
            session_id=SESSION_ID,
            sample_rate=16_000,
            channels=1,
            segment_max_samples=2,
        )
