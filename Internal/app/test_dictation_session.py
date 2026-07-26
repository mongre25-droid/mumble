"""Stage A behaviour tests for durable logical dictation sessions."""

import hashlib
import json
from pathlib import Path
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


def test_recovery_reclaims_a_range_crashed_after_segment_reservation(tmp_path):
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

    recovered = DurableDictationSession.open(tmp_path, SESSION_ID)
    assert recovered.read_manifest()["segments"] == []
    assert recovered.read_manifest()["next_sample"] == 0

    sealed = recovered.append_pcm16(pcm)
    assert sealed["sample_start"] == 0
    assert sealed["sample_end"] == 2


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
        if not recovered.read_manifest()["segments"]:
            recovered.append_pcm16(b"\x01\x00\x02\x00")
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
