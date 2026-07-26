"""Crash-recoverable, content-free manifests for logical dictation sessions.

Stage A deliberately owns durable PCM16 recovery segments and finalisation
identity only.  Capture queues, live partials, transcript assembly, retention UI,
and over-ten-minute operation are later Issue #16 stages.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import branding


SCHEMA = "mumble.dictation-session.v1"
PCM16_ENCODING = "pcm_s16le"
DEFAULT_SEGMENT_SECONDS = 30
OPAQUE_ID = re.compile(r"\A[0-9a-f]{32}\Z")
SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
MANIFEST_FIELDS = frozenset({
    "schema",
    "session_id",
    "created_unix_ns",
    "state",
    "audio",
    "segment_max_samples",
    "next_sample",
    "segments",
    "finalization",
})
AUDIO_FIELDS = frozenset({"sample_rate", "channels", "encoding"})
SEGMENT_FIELDS = frozenset({
    "number",
    "state",
    "sample_start",
    "sample_end",
    "sample_count",
    "byte_count",
    "sha256",
    "filename",
})
FINALIZATION_FIELDS = frozenset({
    "state",
    "owner_id",
    "operation_id",
    "history_state",
    "history_record_id",
    "insertion_state",
    "insertion_outcome",
})
INSERTION_OUTCOMES = frozenset({
    "confirmed",
    "sent_unconfirmed",
    "not_sent",
    "uncertain",
    "saved_only",
})
STATE_TRANSITIONS = (
    "session_created",
    "segment_reserved",
    "segment_sealed",
    "finalization_claimed",
    "history_committed",
    "insertion_claimed",
    "finalization_completed",
)

_SESSION_LOCKS = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path):
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


@contextlib.contextmanager
def _exclusive_session_lock(path: Path, timeout=3.0):
    """Hold one thread-and-process lock for a complete session operation."""
    thread_lock = _thread_lock(path)
    if not thread_lock.acquire(timeout=max(0.0, float(timeout))):
        yield False
        return
    handle = None
    locked = False
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = open(path, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, float(timeout))
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                locked = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield locked
    finally:
        if locked and handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if handle is not None:
            handle.close()
        thread_lock.release()


class DictationSessionError(RuntimeError):
    """Base error for a session that cannot be trusted or advanced."""


class FinalizationOwnershipError(DictationSessionError):
    """A second owner or operation attempted to finalize one session."""


class InjectedCrash(RuntimeError):
    """Deterministic test-only interruption at a named durable boundary."""


class DeterministicFaultInjector:
    """Raise once when the requested before/after transition point is reached."""

    def __init__(self, crash_at):
        self.crash_at = str(crash_at)
        self.triggered = False

    def __call__(self, point):
        if not self.triggered and point == self.crash_at:
            self.triggered = True
            raise InjectedCrash(point)


def _require_opaque_id(value: str, field: str) -> str:
    if not isinstance(value, str) or OPAQUE_ID.fullmatch(value) is None:
        raise ValueError(f"invalid_{field}")
    return value


def _fsync_directory(path: Path) -> None:
    """Best-effort directory flush after the required file flush and replace."""
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise DictationSessionError("temporary_prepare_failed") from error
    try:
        with open(temporary, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise DictationSessionError("temporary_write_failed") from error
    branding.protect_private_path(str(temporary))
    os.replace(temporary, path)
    branding.protect_private_path(str(path))
    _fsync_directory(path.parent)


def _atomic_json(path: Path, value: dict) -> None:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    _atomic_write(path, payload)


class DurableDictationSession:
    """Public Stage A seam for one logical dictation's recovery state."""

    def __init__(self, root: Path, session_id: str, fault_injector=None):
        self.root = Path(root)
        self.session_id = _require_opaque_id(session_id, "session_id")
        self.path = self.root / self.session_id
        self.manifest_path = self.path / "manifest.json"
        self.lock_path = self.root / f".{self.session_id}.session.lock"
        self.fault_injector = fault_injector

    def _fault(self, point):
        if self.fault_injector is not None:
            self.fault_injector(point)

    def _require_session_contained(self):
        root = self.root.resolve(strict=False)
        resolved = self.path.resolve(strict=False)
        if resolved.parent != root:
            raise DictationSessionError("session_path_outside_root")

    @contextlib.contextmanager
    def _locked(self):
        with _exclusive_session_lock(self.lock_path) as acquired:
            if not acquired:
                raise DictationSessionError("session_lock_unavailable")
            yield

    def _transition_locked(self, name, mutate):
        current = self._read_manifest_unlocked()
        updated = copy.deepcopy(current)
        changed = mutate(updated)
        if changed is False:
            return current
        _atomic_json(self.manifest_path, updated)
        self._fault(f"after:{name}")
        return updated

    @classmethod
    def create(
        cls,
        root,
        *,
        session_id,
        sample_rate,
        channels,
        segment_max_samples,
        fault_injector=None,
    ):
        session = cls(
            Path(root), session_id, fault_injector=fault_injector
        )
        sample_rate = int(sample_rate)
        channels = int(channels)
        segment_max_samples = int(segment_max_samples)
        if sample_rate <= 0:
            raise ValueError("invalid_sample_rate")
        if channels <= 0:
            raise ValueError("invalid_channels")
        if segment_max_samples <= 0:
            raise ValueError("invalid_segment_max_samples")
        session._fault("before:session_created")
        session.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        branding.protect_private_path(str(session.root), directory=True)
        with session._locked():
            if session.path.is_symlink():
                raise DictationSessionError("session_path_invalid")
            session.path.mkdir(mode=0o700, exist_ok=True)
            session._require_session_contained()
            branding.protect_private_path(str(session.path), directory=True)
            if session.manifest_path.is_file():
                manifest = session._read_manifest_unlocked()
                audio = manifest["audio"]
                if (audio["sample_rate"] != sample_rate
                        or audio["channels"] != channels
                        or manifest["segment_max_samples"]
                        != segment_max_samples):
                    raise DictationSessionError(
                        "session_create_parameters_mismatch")
                session._recover_unlocked()
                return session
            entries = list(session.path.iterdir())
            unsafe_entries = [
                entry for entry in entries
                if entry.name != "manifest.json.tmp"
            ]
            if unsafe_entries:
                raise DictationSessionError(
                    "incomplete_session_contains_recoverable_data")
            session._fault("after:session_directory_created")
            manifest = {
                "schema": SCHEMA,
                "session_id": session.session_id,
                "created_unix_ns": time.time_ns(),
                "state": "capturing",
                "audio": {
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "encoding": PCM16_ENCODING,
                },
                "segment_max_samples": segment_max_samples,
                "next_sample": 0,
                "segments": [],
                "finalization": {
                    "state": "unclaimed",
                    "owner_id": None,
                    "operation_id": None,
                    "history_state": "not_committed",
                    "history_record_id": None,
                    "insertion_state": "not_requested",
                    "insertion_outcome": None,
                },
            }
            _atomic_json(session.manifest_path, manifest)
            session._fault("after:session_created")
        return session

    @classmethod
    def open(cls, root, session_id, *, fault_injector=None):
        session = cls(Path(root), session_id, fault_injector=fault_injector)
        if session.path.is_symlink() or not session.path.is_dir():
            raise DictationSessionError("session_not_found")
        session._require_session_contained()
        with session._locked():
            session._recover_unlocked()
        return session

    @classmethod
    def discover(cls, root, *, on_error=None):
        """Open every trusted session directory after a process restart."""
        root = Path(root)
        if not root.is_dir():
            return []
        recovered = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if (path.is_symlink() or not path.is_dir()
                    or OPAQUE_ID.fullmatch(path.name) is None):
                continue
            try:
                session = cls.open(root, path.name)
                manifest = session.read_manifest()
                if any(
                        segment.get("state") != "sealed"
                        for segment in manifest["segments"]):
                    if on_error is not None:
                        on_error({
                            "session_id": path.name,
                            "error": "segment_unresolved",
                        })
                    continue
                recovered.append(session)
            except DictationSessionError as error:
                if on_error is not None:
                    on_error({
                        "session_id": path.name,
                        "error": str(error),
                    })
        return recovered

    def read_manifest(self):
        with self._locked():
            return self._read_manifest_unlocked()

    def _read_manifest_unlocked(self):
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise DictationSessionError("manifest_unreadable") from error
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            raise DictationSessionError("manifest_schema_invalid")
        if manifest.get("session_id") != self.session_id:
            raise DictationSessionError("manifest_session_mismatch")
        self._validate_manifest_header(manifest)
        return manifest

    def _validate_manifest_header(self, manifest):
        if set(manifest) != MANIFEST_FIELDS:
            raise DictationSessionError("manifest_fields_invalid")
        if not self._is_positive_int(manifest.get("created_unix_ns")):
            raise DictationSessionError("manifest_created_time_invalid")
        audio = manifest.get("audio")
        if (not isinstance(audio, dict)
                or set(audio) != AUDIO_FIELDS
                or audio.get("encoding") != PCM16_ENCODING
                or not self._is_positive_int(audio.get("sample_rate"))
                or not self._is_positive_int(audio.get("channels"))):
            raise DictationSessionError("manifest_audio_invalid")
        if not self._is_positive_int(manifest.get("segment_max_samples")):
            raise DictationSessionError("manifest_segment_bound_invalid")
        if (not isinstance(manifest.get("next_sample"), int)
                or isinstance(manifest.get("next_sample"), bool)
                or manifest.get("next_sample") < 0):
            raise DictationSessionError("manifest_next_sample_invalid")
        if manifest.get("state") not in ("capturing", "finalizing", "complete"):
            raise DictationSessionError("manifest_state_invalid")
        finalization = manifest.get("finalization")
        if (not isinstance(finalization, dict)
                or set(finalization) != FINALIZATION_FIELDS):
            raise DictationSessionError("manifest_finalization_invalid")
        final_state = finalization.get("state")
        if final_state not in ("unclaimed", "claimed", "complete"):
            raise DictationSessionError("manifest_finalization_invalid")
        if final_state == "unclaimed":
            if (finalization.get("owner_id") is not None
                    or finalization.get("operation_id") is not None):
                raise DictationSessionError("manifest_finalization_invalid")
        elif (OPAQUE_ID.fullmatch(str(finalization.get("owner_id") or "")) is None
              or OPAQUE_ID.fullmatch(
                  str(finalization.get("operation_id") or "")) is None):
            raise DictationSessionError("manifest_finalization_invalid")
        if finalization.get("history_state") not in (
                "not_committed", "committed"):
            raise DictationSessionError("manifest_finalization_invalid")
        if finalization.get("history_state") == "not_committed":
            if finalization.get("history_record_id") is not None:
                raise DictationSessionError("history_identity_mismatch")
        elif finalization.get("history_record_id") != self.session_id:
            raise DictationSessionError("history_identity_mismatch")
        if finalization.get("insertion_state") not in (
                "not_requested", "requested"):
            raise DictationSessionError("manifest_finalization_invalid")
        outcome = finalization.get("insertion_outcome")
        if outcome is not None and outcome not in INSERTION_OUTCOMES:
            raise DictationSessionError("manifest_finalization_invalid")
        state = manifest.get("state")
        expected_final_state = {
            "capturing": "unclaimed",
            "finalizing": "claimed",
            "complete": "complete",
        }[state]
        if final_state != expected_final_state:
            raise DictationSessionError("manifest_state_combination_invalid")
        history_state = finalization.get("history_state")
        insertion_state = finalization.get("insertion_state")
        if (history_state == "not_committed"
                and insertion_state != "not_requested"):
            raise DictationSessionError("manifest_state_combination_invalid")
        if insertion_state == "not_requested" and outcome is not None:
            raise DictationSessionError("manifest_finalization_invalid")
        if final_state == "unclaimed" and (
                history_state != "not_committed"
                or insertion_state != "not_requested"
                or outcome is not None):
            raise DictationSessionError("manifest_state_combination_invalid")
        if final_state == "claimed" and outcome is not None:
            raise DictationSessionError("manifest_state_combination_invalid")
        if final_state == "complete" and (
                history_state != "committed"
                or insertion_state != "requested"
                or outcome not in INSERTION_OUTCOMES):
            raise DictationSessionError("manifest_state_combination_invalid")
        self._validate_segment_records(manifest)

    def _validate_segment_records(self, manifest):
        segments = manifest.get("segments")
        if not isinstance(segments, list):
            raise DictationSessionError("segments_invalid")
        expected_start = 0
        bound = manifest["segment_max_samples"]
        channels = manifest["audio"]["channels"]
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict) or set(segment) != SEGMENT_FIELDS:
                raise DictationSessionError("segment_fields_invalid")
            count = segment.get("sample_count")
            if not self._is_positive_int(count):
                raise DictationSessionError("segment_sample_count_invalid")
            if count > bound:
                raise DictationSessionError("segment_bound_exceeded")
            if (segment.get("number") != index
                    or isinstance(segment.get("number"), bool)
                    or segment.get("sample_start") != expected_start
                    or isinstance(segment.get("sample_start"), bool)
                    or segment.get("sample_end") != expected_start + count
                    or isinstance(segment.get("sample_end"), bool)):
                raise DictationSessionError("segment_ranges_not_contiguous")
            if segment.get("byte_count") != count * channels * 2:
                raise DictationSessionError("segment_pcm_length_invalid")
            if (not isinstance(segment.get("sha256"), str)
                    or SHA256_HEX.fullmatch(segment["sha256"]) is None):
                raise DictationSessionError("segment_checksum_invalid")
            state = segment.get("state")
            if state not in ("writing", "sealed"):
                raise DictationSessionError("segment_state_invalid")
            if state == "writing" and (
                    manifest["state"] != "capturing"
                    or index != len(segments) - 1):
                raise DictationSessionError("segment_unsealed")
            self._segment_path(segment, index)
            expected_start += count
        if manifest.get("next_sample") != expected_start:
            raise DictationSessionError("next_sample_mismatch")

    @staticmethod
    def _is_positive_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def append_pcm16(self, payload: bytes):
        if not isinstance(payload, bytes):
            raise TypeError("pcm16_payload_must_be_bytes")
        self._fault("before:segment_reserved")
        with self._locked():
            manifest = self._read_manifest_unlocked()
            if manifest.get("state") != "capturing":
                raise DictationSessionError("session_not_capturing")
            if any(
                    segment.get("state") != "sealed"
                    for segment in manifest["segments"]):
                raise DictationSessionError("segment_unresolved")
            channels = int(manifest["audio"]["channels"])
            frame_bytes = channels * 2
            if not payload or len(payload) % frame_bytes:
                raise ValueError("invalid_pcm16_payload")
            sample_count = len(payload) // frame_bytes
            if sample_count > int(manifest["segment_max_samples"]):
                raise ValueError("segment_too_large")
            start = int(manifest["next_sample"])
            number = len(manifest["segments"])
            filename = f"segment-{number:08d}.pcm"
            segment = {
                "number": number,
                "state": "writing",
                "sample_start": start,
                "sample_end": start + sample_count,
                "sample_count": sample_count,
                "byte_count": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "filename": filename,
            }
            target = self._segment_path(segment, number)
            if target.exists():
                raise DictationSessionError("immutable_segment_exists")

            def reserve(updated):
                if (updated.get("state") != "capturing"
                        or int(updated.get("next_sample", -1)) != start
                        or len(updated.get("segments", [])) != number):
                    raise DictationSessionError(
                        "session_changed_during_append")
                updated["segments"].append(copy.deepcopy(segment))
                updated["next_sample"] = segment["sample_end"]

            self._transition_locked("segment_reserved", reserve)
            _atomic_write(target, payload)

            def seal(updated):
                current = updated.get("segments", [])
                if number >= len(current) or current[number] != segment:
                    raise DictationSessionError(
                        "segment_reservation_changed")
                current[number]["state"] = "sealed"

            self._fault("before:segment_sealed")
            updated = self._transition_locked("segment_sealed", seal)
            return copy.deepcopy(updated["segments"][number])

    def reconcile_unresolved_pcm16(self, payload: bytes, *, channels: int):
        """Seal the existing unresolved range only when its PCM16 bytes match."""
        if not isinstance(payload, bytes):
            raise TypeError("pcm16_payload_must_be_bytes")
        if (not isinstance(channels, int) or isinstance(channels, bool)
                or channels <= 0):
            raise ValueError("invalid_channels")
        with self._locked():
            manifest = self._read_manifest_unlocked()
            if manifest.get("state") != "capturing":
                raise DictationSessionError("session_not_capturing")
            segments = manifest.get("segments")
            if (not isinstance(segments, list) or not segments
                    or segments[-1].get("state") != "writing"):
                raise DictationSessionError("no_unresolved_segment")

            segment = copy.deepcopy(segments[-1])
            expected_channels = int(manifest["audio"]["channels"])
            if channels != expected_channels:
                raise DictationSessionError(
                    "reconciliation_channels_mismatch")
            if len(payload) != int(segment["byte_count"]):
                raise DictationSessionError("reconciliation_length_mismatch")
            frame_bytes = expected_channels * 2
            if (not payload or len(payload) % frame_bytes
                    or len(payload) // frame_bytes
                    != int(segment["sample_count"])):
                raise DictationSessionError("reconciliation_length_mismatch")
            if hashlib.sha256(payload).hexdigest() != segment["sha256"]:
                raise DictationSessionError(
                    "reconciliation_checksum_mismatch")

            number = int(segment["number"])
            target = self._segment_path(segment, number)
            if target.exists():
                self._validate_segment_file(segment, target)
            else:
                _atomic_write(target, payload)

            def seal(updated):
                current = updated.get("segments", [])
                if (number >= len(current)
                        or current[number] != segment
                        or current[number].get("state") != "writing"):
                    raise DictationSessionError(
                        "segment_reservation_changed")
                current[number]["state"] = "sealed"

            self._fault("before:segment_sealed")
            updated = self._transition_locked("segment_sealed", seal)
            return copy.deepcopy(updated["segments"][number])

    def claim_finalization(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        self._fault("before:finalization_claimed")
        with self._locked():
            manifest = self._read_manifest_unlocked()
            self._require_all_segments_sealed(manifest)
            manifest = self._verify_unlocked()
            current = manifest.get("finalization") or {}
            state = current.get("state")
            if state != "unclaimed":
                self._require_finalization_owner(
                    current, owner_id, operation_id)
                return copy.deepcopy(current)

            def claim(updated):
                finalization = updated.get("finalization") or {}
                if finalization.get("state") != "unclaimed":
                    self._require_finalization_owner(
                        finalization, owner_id, operation_id
                    )
                    return
                self._require_all_segments_sealed(updated)
                finalization["state"] = "claimed"
                finalization["owner_id"] = owner_id
                finalization["operation_id"] = operation_id
                updated["finalization"] = finalization
                updated["state"] = "finalizing"

            updated = self._transition_locked("finalization_claimed", claim)
            return copy.deepcopy(updated["finalization"])

    def claim_final_insertion(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        self._fault("before:insertion_claimed")
        with self._locked():
            manifest = self._verify_unlocked()
            current = manifest.get("finalization") or {}
            self._require_finalization_owner(
                current, owner_id, operation_id)
            if current.get("history_state") != "committed":
                raise DictationSessionError("history_not_committed")
            if current.get("insertion_state") == "requested":
                return False
            if current.get("insertion_state") != "not_requested":
                raise DictationSessionError("insertion_state_invalid")

            claimed = {"value": True}

            def claim(updated):
                finalization = updated.get("finalization") or {}
                self._require_finalization_owner(
                    finalization, owner_id, operation_id
                )
                if finalization.get("insertion_state") == "requested":
                    claimed["value"] = False
                    return False
                if finalization.get("insertion_state") != "not_requested":
                    raise DictationSessionError("insertion_state_invalid")
                # Persist before the caller reaches native input. After a crash
                # the request is potentially sent and is never resubmitted.
                finalization["insertion_state"] = "requested"

            self._transition_locked("insertion_claimed", claim)
            return claimed["value"]

    def mark_history_committed(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        self._fault("before:history_committed")
        with self._locked():
            manifest = self._verify_unlocked()
            current = manifest.get("finalization") or {}
            self._require_finalization_owner(
                current, owner_id, operation_id)
            if current.get("history_state") == "committed":
                if current.get("history_record_id") != self.session_id:
                    raise DictationSessionError("history_identity_mismatch")
                return False
            if current.get("history_state") != "not_committed":
                raise DictationSessionError("history_state_invalid")

            committed = {"value": True}

            def commit(updated):
                finalization = updated.get("finalization") or {}
                self._require_finalization_owner(
                    finalization, owner_id, operation_id
                )
                if finalization.get("history_state") == "committed":
                    if finalization.get("history_record_id") != self.session_id:
                        raise DictationSessionError(
                            "history_identity_mismatch")
                    committed["value"] = False
                    return False
                if finalization.get("history_state") != "not_committed":
                    raise DictationSessionError("history_state_invalid")
                # The logical session ID is the future History idempotency key;
                # no transcript text is stored in this recovery manifest.
                finalization["history_state"] = "committed"
                finalization["history_record_id"] = self.session_id

            self._transition_locked("history_committed", commit)
            return committed["value"]

    def complete_finalization(
        self, owner_id, operation_id, *, insertion_outcome
    ):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        if insertion_outcome not in INSERTION_OUTCOMES:
            raise ValueError("invalid_insertion_outcome")
        self._fault("before:finalization_completed")
        with self._locked():
            manifest = self._verify_unlocked()
            current = manifest.get("finalization") or {}
            self._require_finalization_owner(
                current, owner_id, operation_id)
            if current.get("state") == "complete":
                if current.get("insertion_outcome") != insertion_outcome:
                    raise DictationSessionError(
                        "finalization_outcome_changed")
                return copy.deepcopy(current)
            if current.get("history_state") != "committed":
                raise DictationSessionError("history_not_committed")
            if current.get("insertion_state") != "requested":
                raise DictationSessionError("insertion_not_claimed")

            def complete(updated):
                finalization = updated.get("finalization") or {}
                self._require_finalization_owner(
                    finalization, owner_id, operation_id
                )
                if finalization.get("state") != "claimed":
                    raise DictationSessionError("finalization_state_invalid")
                if finalization.get("history_state") != "committed":
                    raise DictationSessionError("history_not_committed")
                if finalization.get("insertion_state") != "requested":
                    raise DictationSessionError("insertion_not_claimed")
                finalization["state"] = "complete"
                finalization["insertion_outcome"] = insertion_outcome
                updated["state"] = "complete"

            updated = self._transition_locked(
                "finalization_completed", complete)
            return copy.deepcopy(updated["finalization"])

    def verify(self):
        """Validate schema, ordered ownership, file sizes, and checksums."""
        with self._locked():
            return self._verify_unlocked()

    def discard_unfinalized(self):
        """Remove a verified session only before any finalisation owner exists."""
        with self._locked():
            manifest = self._verify_unlocked()
            finalization = manifest["finalization"]
            if (manifest["state"] != "capturing"
                    or finalization["state"] != "unclaimed"):
                raise DictationSessionError("session_discard_not_allowed")
            for segment in manifest["segments"]:
                (self.path / segment["filename"]).unlink()
            self.manifest_path.unlink()
            self.path.rmdir()
            _fsync_directory(self.root)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(self.root)

    def _verify_unlocked(self):
        manifest = self._read_manifest_unlocked()
        segments = manifest.get("segments")
        if not isinstance(segments, list):
            raise DictationSessionError("segments_invalid")
        expected_start = 0
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                raise DictationSessionError("segment_invalid")
            count = segment.get("sample_count")
            if (not isinstance(count, int) or count <= 0
                    or segment.get("number") != index
                    or segment.get("state") != "sealed"
                    or segment.get("sample_start") != expected_start
                    or segment.get("sample_end") != expected_start + count):
                raise DictationSessionError("segment_ranges_not_contiguous")
            self._validate_segment_file(
                segment, self._segment_path(segment, index)
            )
            expected_start += count
        if manifest.get("next_sample") != expected_start:
            raise DictationSessionError("next_sample_mismatch")
        self._require_exact_segment_files(segments)
        return copy.deepcopy(manifest)

    @staticmethod
    def _require_finalization_owner(finalization, owner_id, operation_id):
        if (finalization.get("state") == "unclaimed"
                or finalization.get("owner_id") != owner_id
                or finalization.get("operation_id") != operation_id):
            raise FinalizationOwnershipError("finalization_owner_mismatch")

    @staticmethod
    def _require_all_segments_sealed(manifest):
        segments = manifest.get("segments")
        if not isinstance(segments, list):
            raise DictationSessionError("segments_invalid")
        if any(
                not isinstance(segment, dict)
                or segment.get("state") != "sealed"
                for segment in segments):
            raise DictationSessionError("segment_unsealed")

    def _recover_unlocked(self):
        manifest = self._read_manifest_unlocked()
        protected_temporaries = {
            f'{segment["filename"]}.tmp'
            for segment in manifest["segments"]
            if segment["state"] == "writing"
        }
        for temporary in self.path.glob("*.tmp"):
            if temporary.name in protected_temporaries:
                continue
            try:
                temporary.unlink()
            except OSError as error:
                raise DictationSessionError("temporary_cleanup_failed") from error

        segments = manifest.get("segments")
        if not isinstance(segments, list):
            raise DictationSessionError("segments_invalid")
        expected_start = 0
        recovered = []
        changed = False
        for index, raw_segment in enumerate(segments):
            if not isinstance(raw_segment, dict):
                raise DictationSessionError("segment_invalid")
            segment = copy.deepcopy(raw_segment)
            if (segment.get("number") != index
                    or segment.get("sample_start") != expected_start
                    or segment.get("sample_end") != (
                        expected_start + segment.get("sample_count", -1))):
                raise DictationSessionError("segment_ranges_not_contiguous")
            target = self._segment_path(segment, index)
            state = segment.get("state")
            if state == "writing" and not target.is_file():
                if index != len(segments) - 1:
                    raise DictationSessionError("missing_nonfinal_segment")
                recovered.append(segment)
                expected_start = int(segment["sample_end"])
                continue
            if state not in ("writing", "sealed"):
                raise DictationSessionError("segment_state_invalid")
            self._validate_segment_file(segment, target)
            if state == "writing":
                segment["state"] = "sealed"
                changed = True
            recovered.append(segment)
            expected_start = int(segment["sample_end"])

        if int(manifest.get("next_sample", -1)) != expected_start:
            changed = True
        self._require_no_unaccounted_segment_files(recovered)
        if changed:
            manifest["segments"] = recovered
            manifest["next_sample"] = expected_start
            _atomic_json(self.manifest_path, manifest)

    def _require_exact_segment_files(self, segments):
        expected = {segment["filename"] for segment in segments}
        actual = {
            entry.name for entry in self.path.iterdir()
            if entry.name.startswith("segment-")
            and entry.name.endswith(".pcm")
        }
        if actual != expected:
            raise DictationSessionError("unexpected_segment_file")

    def _require_no_unaccounted_segment_files(self, segments):
        expected = {segment["filename"] for segment in segments}
        actual = {
            entry.name for entry in self.path.iterdir()
            if entry.name.startswith("segment-")
            and entry.name.endswith(".pcm")
        }
        if not actual.issubset(expected):
            raise DictationSessionError("unexpected_segment_file")

    @staticmethod
    def _validate_segment_file(segment, target):
        try:
            payload = target.read_bytes()
        except OSError as error:
            raise DictationSessionError("segment_missing") from error
        if len(payload) != segment.get("byte_count"):
            raise DictationSessionError("segment_size_mismatch")
        if hashlib.sha256(payload).hexdigest() != segment.get("sha256"):
            raise DictationSessionError("segment_checksum_mismatch")

    def _segment_path(self, segment, number):
        expected = f"segment-{number:08d}.pcm"
        if segment.get("filename") != expected:
            raise DictationSessionError("segment_filename_invalid")
        target = self.path / expected
        self._require_session_contained()
        session_root = self.path.resolve(strict=False)
        resolved = target.resolve(strict=False)
        if target.is_symlink() or resolved.parent != session_root:
            raise DictationSessionError("segment_path_outside_session")
        return target


def default_recovery_root() -> Path:
    """Small private location beside Mumble's other per-user durable data."""
    return Path(branding.DATA_DIR) / "dictation_recovery"


def default_segment_max_samples(sample_rate: int) -> int:
    """Return Stage A's bounded default segment size in sample frames."""
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValueError("invalid_sample_rate")
    return sample_rate * DEFAULT_SEGMENT_SECONDS
