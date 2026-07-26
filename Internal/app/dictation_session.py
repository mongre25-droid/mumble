"""Crash-recoverable, content-free manifests for logical dictation sessions.

Stage A deliberately owns durable PCM16 recovery segments and finalisation
identity only.  Capture queues, live partials, transcript assembly, retention UI,
and over-ten-minute operation are later Issue #16 stages.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time

import branding
from storage_lock import exclusive_file_lock


SCHEMA = "mumble.dictation-session.v1"
PCM16_ENCODING = "pcm_s16le"
DEFAULT_SEGMENT_SECONDS = 30
OPAQUE_ID = re.compile(r"\A[0-9a-f]{32}\Z")
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
    with open(temporary, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
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
        self.fault_injector = fault_injector

    def _fault(self, point):
        if self.fault_injector is not None:
            self.fault_injector(point)

    def _transition(self, name, mutate):
        self._fault(f"before:{name}")
        with exclusive_file_lock(self.manifest_path) as acquired:
            if not acquired:
                raise DictationSessionError("manifest_lock_unavailable")
            current = self.read_manifest()
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
        try:
            session.path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise DictationSessionError("session_already_exists") from error
        branding.protect_private_path(str(session.path), directory=True)
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
        if not session.path.is_dir():
            raise DictationSessionError("session_not_found")
        session._recover()
        return session

    @classmethod
    def discover(cls, root):
        """Open every trusted session directory after a process restart."""
        root = Path(root)
        if not root.is_dir():
            return []
        recovered = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if (path.is_symlink() or not path.is_dir()
                    or OPAQUE_ID.fullmatch(path.name) is None):
                continue
            recovered.append(cls.open(root, path.name))
        return recovered

    def read_manifest(self):
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
        audio = manifest.get("audio")
        if (not isinstance(audio, dict)
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
        if not isinstance(finalization, dict):
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
        if (finalization.get("history_state") == "committed"
                and finalization.get("history_record_id") != self.session_id):
            raise DictationSessionError("history_identity_mismatch")
        if finalization.get("insertion_state") not in (
                "not_requested", "requested"):
            raise DictationSessionError("manifest_finalization_invalid")
        outcome = finalization.get("insertion_outcome")
        if outcome is not None and outcome not in INSERTION_OUTCOMES:
            raise DictationSessionError("manifest_finalization_invalid")
        if ((manifest.get("state") == "complete")
                != (final_state == "complete")):
            raise DictationSessionError("manifest_finalization_invalid")

    @staticmethod
    def _is_positive_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def append_pcm16(self, payload: bytes):
        if not isinstance(payload, bytes):
            raise TypeError("pcm16_payload_must_be_bytes")
        manifest = self.read_manifest()
        if manifest.get("state") != "capturing":
            raise DictationSessionError("session_not_capturing")
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
        target = self.path / filename
        if target.exists():
            raise DictationSessionError("immutable_segment_exists")

        def reserve(updated):
            if (updated.get("state") != "capturing"
                    or int(updated.get("next_sample", -1)) != start
                    or len(updated.get("segments", [])) != number):
                raise DictationSessionError("session_changed_during_append")
            updated["segments"].append(copy.deepcopy(segment))
            updated["next_sample"] = segment["sample_end"]

        self._transition("segment_reserved", reserve)
        _atomic_write(target, payload)

        def seal(updated):
            current = updated.get("segments", [])
            if number >= len(current) or current[number] != segment:
                raise DictationSessionError("segment_reservation_changed")
            current[number]["state"] = "sealed"

        updated = self._transition("segment_sealed", seal)
        return copy.deepcopy(updated["segments"][number])

    def claim_finalization(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        manifest = self.read_manifest()
        current = manifest.get("finalization") or {}
        state = current.get("state")
        if state != "unclaimed":
            self._require_finalization_owner(current, owner_id, operation_id)
            return copy.deepcopy(current)

        def claim(updated):
            finalization = updated.get("finalization") or {}
            if finalization.get("state") != "unclaimed":
                self._require_finalization_owner(
                    finalization, owner_id, operation_id
                )
                return
            finalization["state"] = "claimed"
            finalization["owner_id"] = owner_id
            finalization["operation_id"] = operation_id
            updated["finalization"] = finalization
            updated["state"] = "finalizing"

        updated = self._transition("finalization_claimed", claim)
        return copy.deepcopy(updated["finalization"])

    def claim_final_insertion(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        manifest = self.read_manifest()
        current = manifest.get("finalization") or {}
        self._require_finalization_owner(current, owner_id, operation_id)
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
            # Persist before the caller reaches native input. After a crash the
            # request is treated as potentially sent and is never resubmitted.
            finalization["insertion_state"] = "requested"

        self._transition("insertion_claimed", claim)
        return claimed["value"]

    def mark_history_committed(self, owner_id, operation_id):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        manifest = self.read_manifest()
        current = manifest.get("finalization") or {}
        self._require_finalization_owner(current, owner_id, operation_id)
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
                    raise DictationSessionError("history_identity_mismatch")
                committed["value"] = False
                return False
            if finalization.get("history_state") != "not_committed":
                raise DictationSessionError("history_state_invalid")
            # The logical session ID is the future History idempotency key; no
            # transcript text is stored in this recovery manifest.
            finalization["history_state"] = "committed"
            finalization["history_record_id"] = self.session_id

        self._transition("history_committed", commit)
        return committed["value"]

    def complete_finalization(
        self, owner_id, operation_id, *, insertion_outcome
    ):
        owner_id = _require_opaque_id(owner_id, "owner_id")
        operation_id = _require_opaque_id(operation_id, "operation_id")
        if insertion_outcome not in INSERTION_OUTCOMES:
            raise ValueError("invalid_insertion_outcome")
        manifest = self.read_manifest()
        current = manifest.get("finalization") or {}
        self._require_finalization_owner(current, owner_id, operation_id)
        if current.get("state") == "complete":
            if current.get("insertion_outcome") != insertion_outcome:
                raise DictationSessionError("finalization_outcome_changed")
            return copy.deepcopy(current)
        if current.get("history_state") != "committed":
            raise DictationSessionError("history_not_committed")

        def complete(updated):
            finalization = updated.get("finalization") or {}
            self._require_finalization_owner(
                finalization, owner_id, operation_id
            )
            if finalization.get("state") != "claimed":
                raise DictationSessionError("finalization_state_invalid")
            finalization["state"] = "complete"
            finalization["insertion_outcome"] = insertion_outcome
            updated["state"] = "complete"

        updated = self._transition("finalization_completed", complete)
        return copy.deepcopy(updated["finalization"])

    def verify(self):
        """Validate schema, ordered ownership, file sizes, and checksums."""
        manifest = self.read_manifest()
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
        return copy.deepcopy(manifest)

    @staticmethod
    def _require_finalization_owner(finalization, owner_id, operation_id):
        if (finalization.get("state") == "unclaimed"
                or finalization.get("owner_id") != owner_id
                or finalization.get("operation_id") != operation_id):
            raise FinalizationOwnershipError("finalization_owner_mismatch")

    def _recover(self):
        for temporary in self.path.glob("*.tmp"):
            try:
                temporary.unlink()
            except OSError as error:
                raise DictationSessionError("temporary_cleanup_failed") from error

        manifest = self.read_manifest()
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
                changed = True
                break
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
        if changed:
            manifest["segments"] = recovered
            manifest["next_sample"] = expected_start
            _atomic_json(self.manifest_path, manifest)

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
        return self.path / expected


def default_recovery_root() -> Path:
    """Small private location beside Mumble's other per-user durable data."""
    return Path(branding.DATA_DIR) / "dictation_recovery"


def default_segment_max_samples(sample_rate: int) -> int:
    """Return Stage A's bounded default segment size in sample frames."""
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValueError("invalid_sample_rate")
    return sample_rate * DEFAULT_SEGMENT_SECONDS
