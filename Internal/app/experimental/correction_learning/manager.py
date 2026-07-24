"""Settings integration and privacy-minimal persistent undo for corrections."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
import uuid

from .engine import analyze_correction


_SCHEMA_VERSION = 1
_MAX_SESSIONS = 250
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_state() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "sessions": []}


class _AtomicSessionStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        supplied = Path(path).expanduser()
        if supplied.suffix.lower() == ".json":
            self.path = supplied.resolve()
        else:
            self.path = (supplied / "correction_learning.json").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._protect(self.path.parent, directory=True)

    @contextlib.contextmanager
    def transaction(self, *, timeout: float = 3.0, stale: float = 30.0):
        """Serialize a complete history read/modify/write across managers."""

        lock_path = self.path.with_name(self.path.name + ".lock")
        token = uuid.uuid4().hex.encode("ascii")
        descriptor: int | None = None
        started = time.monotonic()
        while descriptor is None:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(descriptor, token)
                os.fsync(descriptor)
                self._protect(lock_path, directory=False)
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > stale:
                        lock_path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("correction history is busy") from None
                time.sleep(0.02)
            except OSError as exc:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    try:
                        if lock_path.read_bytes() == token:
                            lock_path.unlink()
                    except OSError:
                        pass
                raise OSError(f"cannot lock correction history: {exc}") from exc
        try:
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            # A token check prevents an old/stale owner from deleting a lock
            # that has since been reclaimed and acquired by another manager.
            try:
                if lock_path.read_bytes() == token:
                    lock_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _protect(path: Path, *, directory: bool) -> None:
        try:
            os.chmod(path, 0o700 if directory else 0o600)
        except OSError:
            pass

    @staticmethod
    def _validate(state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError("correction history must be a JSON object")
        if set(state) != {"schema_version", "sessions"}:
            raise ValueError("correction history has an invalid schema")
        if state.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported correction history schema")
        sessions = state.get("sessions")
        if not isinstance(sessions, list):
            raise ValueError("correction history sessions must be a list")
        if len(sessions) > _MAX_SESSIONS:
            raise ValueError("correction history exceeds its privacy limit")
        seen_ids: set[str] = set()
        for session in sessions:
            if not isinstance(session, dict):
                raise ValueError("invalid correction history session")
            if set(session) != {
                "id",
                "created_at",
                "changes",
                "term_changes",
                "terms_after_hash",
                "undone",
            }:
                raise ValueError("correction history session has an invalid schema")
            if (
                not isinstance(session.get("id"), str)
                or not session["id"]
                or len(session["id"]) > 64
                or session["id"] in seen_ids
            ):
                raise ValueError("correction history session has no id")
            seen_ids.add(session["id"])
            if not isinstance(session.get("created_at"), str):
                raise ValueError("correction history session has no timestamp")
            if not isinstance(session.get("changes"), list):
                raise ValueError("correction history session has invalid changes")
            if not isinstance(session.get("term_changes"), list):
                raise ValueError("correction history session has invalid term changes")
            if not session["changes"] and not session["term_changes"]:
                raise ValueError("correction history session is empty")
            if not isinstance(session.get("terms_after_hash"), str) or not _HASH_RE.fullmatch(
                session["terms_after_hash"]
            ):
                raise ValueError("correction history session has an invalid term revision")
            for change in session["changes"]:
                if not isinstance(change, dict) or set(change) != {
                    "key",
                    "to",
                    "before_present",
                    "before",
                }:
                    raise ValueError("correction history has an invalid mapping delta")
                if (
                    not isinstance(change["key"], str)
                    or not change["key"]
                    or len(change["key"]) > 100
                    or not isinstance(change["to"], str)
                    or not change["to"]
                    or len(change["to"]) > 100
                    or not isinstance(change["before_present"], bool)
                ):
                    raise ValueError("correction history has an invalid mapping delta")
                previous = change["before"]
                if change["before_present"]:
                    if not isinstance(previous, str) or len(previous) > 500:
                        raise ValueError("correction history has an invalid previous mapping")
                elif previous is not None:
                    raise ValueError("correction history has an invalid previous mapping")
            for change in session["term_changes"]:
                if not isinstance(change, dict) or change.get("action") not in {
                    "added",
                    "recased",
                }:
                    raise ValueError("correction history has an invalid term delta")
                expected = {"to", "action"} | (
                    {"before"} if change.get("action") == "recased" else set()
                )
                if set(change) != expected:
                    raise ValueError("correction history has an invalid term delta")
                if (
                    not isinstance(change.get("to"), str)
                    or not change["to"]
                    or len(change["to"]) > 100
                ):
                    raise ValueError("correction history has an invalid term delta")
                if change["action"] == "recased" and (
                    not isinstance(change.get("before"), str)
                    or len(change["before"]) > 500
                ):
                    raise ValueError("correction history has an invalid term delta")
            undone = session["undone"]
            if undone is not None:
                if not isinstance(undone, dict) or set(undone) != {
                    "at",
                    "reverted_mapping_count",
                    "skipped_count",
                }:
                    raise ValueError("correction history has invalid undo metadata")
                if (
                    not isinstance(undone["at"], str)
                    or not isinstance(undone["reverted_mapping_count"], int)
                    or isinstance(undone["reverted_mapping_count"], bool)
                    or undone["reverted_mapping_count"] < 0
                    or not isinstance(undone["skipped_count"], int)
                    or isinstance(undone["skipped_count"], bool)
                    or undone["skipped_count"] < 0
                ):
                    raise ValueError("correction history has invalid undo metadata")
        return state

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_state()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read correction history: {exc}") from exc
        return self._validate(state)

    def save(self, state: dict[str, Any]) -> None:
        state = self._validate(copy.deepcopy(state))
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    state,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._protect(temporary, directory=False)
            os.replace(temporary, self.path)
            self._protect(self.path, directory=False)
            # Make the directory entry durable where the platform supports it.
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass


class CorrectionLearningManager:
    """Learn exact vocabulary from explicit user corrections.

    ``settings`` needs ``get(key, default)`` and either ``update(**values)`` or
    ``set(key, value)``.  A mutable mapping is also accepted, which keeps the
    package easy to test and reuse.
    """

    ENABLED_SETTING = "correction_learning_enabled"

    def __init__(self, settings: Any, path: str | os.PathLike[str]) -> None:
        self.settings = settings
        self._store = _AtomicSessionStore(path)
        self._lock = threading.RLock()
        self._storage_error: str | None = None
        try:
            self._store.load()
        except ValueError as exc:
            self._storage_error = str(exc)

    def _get(self, key: str, default: Any) -> Any:
        getter = getattr(self.settings, "get", None)
        if not callable(getter):
            raise TypeError("settings must provide get(key, default)")
        return getter(key, default)

    @staticmethod
    def _clean_vocabulary(value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            return {}
        return {
            str(source): str(target)
            for source, target in value.items()
            if str(source).strip() and str(target).strip()
        }

    @staticmethod
    def _clean_terms(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(term).strip() for term in value if str(term).strip()]

    def _read_settings(self) -> tuple[dict[str, str], list[str]]:
        return (
            self._clean_vocabulary(self._get("vocabulary", {})),
            self._clean_terms(self._get("vocabulary_terms", [])),
        )

    def _write_settings(
        self,
        vocabulary: dict[str, str],
        terms: list[str],
        *,
        previous: tuple[dict[str, str], list[str]] | None = None,
    ) -> None:
        vocabulary = dict(vocabulary)
        terms = list(terms)
        if isinstance(self.settings, MutableMapping):
            self.settings.update(
                {"vocabulary": vocabulary, "vocabulary_terms": terms}
            )
            return
        updater = getattr(self.settings, "update", None)
        if callable(updater):
            updater(vocabulary=vocabulary, vocabulary_terms=terms)
            return
        setter = getattr(self.settings, "set", None)
        if not callable(setter):
            raise TypeError("settings must provide update(**values) or set(key, value)")
        try:
            setter("vocabulary", vocabulary)
            setter("vocabulary_terms", terms)
        except Exception:
            if previous is not None:
                try:
                    setter("vocabulary", dict(previous[0]))
                    setter("vocabulary_terms", list(previous[1]))
                except Exception:
                    pass
            raise

    def _transact_settings(self, mutator: Any) -> Any:
        """Mutate the freshest vocabulary snapshot available to Settings."""

        atomic_update = getattr(self.settings, "atomic_vocabulary_update", None)
        if callable(atomic_update):
            return atomic_update(mutator)

        # Lightweight Settings-like objects used by tests/embedders have no
        # cross-process store. Preserve the same callback contract locally.
        before_vocabulary, before_terms = self._read_settings()
        vocabulary = dict(before_vocabulary)
        terms = list(before_terms)
        result = mutator(vocabulary, terms)
        if vocabulary != before_vocabulary or terms != before_terms:
            self._write_settings(
                vocabulary,
                terms,
                previous=(before_vocabulary, before_terms),
            )
        return result

    def _load_state(self) -> dict[str, Any]:
        state = self._store.load()
        self._storage_error = None
        return state

    @staticmethod
    def _find_casefold(mapping: Mapping[str, str], source: str) -> str | None:
        folded = source.casefold()
        return next((key for key in mapping if key.casefold() == folded), None)

    def preview(self, original: str, corrected: str) -> dict[str, Any]:
        """Analyze a correction and describe its settings impact without writing."""

        result = analyze_correction(original, corrected)
        try:
            vocabulary, terms = self._read_settings()
        except Exception as exc:
            return {
                **result,
                "ok": False,
                "message": f"Settings are unavailable: {exc}",
                "changes": [],
                "session_id": None,
            }
        term_lookup = {term.casefold(): term for term in terms}
        changes: list[dict[str, Any]] = []
        for candidate in result["changes"]:
            change = dict(candidate)
            existing_key = self._find_casefold(vocabulary, candidate["from"])
            if existing_key is None:
                change["mapping_action"] = "add"
            elif vocabulary[existing_key] == candidate["to"]:
                change["mapping_action"] = "unchanged"
            else:
                change["mapping_action"] = "update"
            existing_term = term_lookup.get(candidate["to"].casefold())
            if existing_term is None:
                change["term_action"] = "add"
            elif existing_term == candidate["to"]:
                change["term_action"] = "unchanged"
            else:
                change["term_action"] = "update_casing"
            changes.append(change)
        return {**result, "changes": changes, "session_id": None}

    def learn(self, original: str, corrected: str) -> dict[str, Any]:
        """Serialize one complete settings/history learning transaction."""

        with self._lock:
            try:
                with self._store.transaction():
                    return self._learn_locked(original, corrected)
            except (OSError, TimeoutError) as exc:
                self._storage_error = str(exc)
                return {
                    "ok": False,
                    "message": f"Correction history is unavailable: {exc}",
                    "changes": [],
                    "session_id": None,
                }

    def _learn_locked(self, original: str, corrected: str) -> dict[str, Any]:
        """Persist safe mappings and corrected terms, returning one undo session."""

        with self._lock:
            preview = self.preview(original, corrected)
            if not preview["ok"]:
                return {
                    "ok": False,
                    "message": preview["message"],
                    "changes": preview["changes"],
                    "session_id": None,
                    "rejected": preview.get("rejected", []),
                }
            try:
                state = self._load_state()
            except (ValueError, OSError) as exc:
                self._storage_error = str(exc)
                return {
                    "ok": False,
                    "message": f"Correction history is unavailable: {exc}",
                    "changes": [],
                    "session_id": None,
                }

            def apply_learning(
                vocabulary: dict[str, str], terms: list[str]
            ) -> dict[str, Any]:
                mapping_records: list[dict[str, Any]] = []
                term_records: list[dict[str, Any]] = []
                applied_changes: list[dict[str, Any]] = []
                handled_terms: set[str] = set()

                for candidate in preview["changes"]:
                    source = candidate["from"]
                    target = candidate["to"]
                    existing_key = self._find_casefold(vocabulary, source)
                    key = existing_key if existing_key is not None else source
                    before_present = existing_key is not None
                    before_target = vocabulary.get(key) if before_present else None
                    mapping_changed = not before_present or before_target != target
                    if mapping_changed:
                        vocabulary[key] = target
                        mapping_records.append(
                            {
                                "key": key,
                                "to": target,
                                "before_present": before_present,
                                "before": before_target,
                            }
                        )

                    target_folded = target.casefold()
                    term_record: dict[str, Any] | None = None
                    if target_folded not in handled_terms:
                        existing_index = next(
                            (
                                index
                                for index, term in enumerate(terms)
                                if term.casefold() == target_folded
                            ),
                            None,
                        )
                        if existing_index is None:
                            terms.append(target)
                            term_record = {"to": target, "action": "added"}
                        elif terms[existing_index] != target:
                            previous_term = terms[existing_index]
                            terms[existing_index] = target
                            term_record = {
                                "to": target,
                                "action": "recased",
                                "before": previous_term,
                            }
                        if term_record is not None:
                            term_records.append(term_record)
                        handled_terms.add(target_folded)

                    if mapping_changed or term_record is not None:
                        applied = dict(candidate)
                        applied["mapping_action"] = (
                            "add" if not before_present else "update"
                        ) if mapping_changed else "unchanged"
                        applied["term_action"] = (
                            "add"
                            if term_record and term_record["action"] == "added"
                            else "update_casing"
                            if term_record
                            else "unchanged"
                        )
                        applied_changes.append(applied)

                return {
                    "mapping_records": mapping_records,
                    "term_records": term_records,
                    "applied_changes": applied_changes,
                    "terms_after_hash": _json_hash(terms),
                }

            try:
                transaction = self._transact_settings(apply_learning)
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"Could not update settings: {exc}",
                    "changes": [],
                    "session_id": None,
                }

            mapping_records = transaction["mapping_records"]
            term_records = transaction["term_records"]
            applied_changes = transaction["applied_changes"]
            terms_after_hash = transaction["terms_after_hash"]
            if not mapping_records and not term_records:
                return {
                    "ok": True,
                    "message": "This correction is already learned.",
                    "changes": [],
                    "session_id": None,
                    "rejected": preview.get("rejected", []),
                }

            session_id = uuid.uuid4().hex
            session = {
                "id": session_id,
                "created_at": _utc_now(),
                # Only exact learned terms and the minimum inverse delta are
                # retained.  The surrounding before/after transcript is never
                # written to disk.
                "changes": mapping_records,
                "term_changes": term_records,
                "terms_after_hash": terms_after_hash,
                "undone": None,
            }
            state["sessions"].append(session)
            if len(state["sessions"]) > _MAX_SESSIONS:
                state["sessions"] = state["sessions"][-_MAX_SESSIONS:]
            try:
                self._store.save(state)
            except (OSError, TypeError, ValueError) as exc:
                self._storage_error = str(exc)
                # Compensate through the same locked merge path. Each learned
                # key is reverted only while it still has our installed value;
                # later manual edits always win.
                def rollback_learning(
                    vocabulary: dict[str, str], terms: list[str]
                ) -> None:
                    conflicting_targets: set[str] = set()
                    for record in reversed(mapping_records):
                        key = record["key"]
                        target = record["to"]
                        if vocabulary.get(key) != target:
                            conflicting_targets.add(target.casefold())
                            continue
                        if record["before_present"]:
                            vocabulary[key] = str(record["before"])
                        else:
                            del vocabulary[key]
                    if _json_hash(terms) != terms_after_hash:
                        return
                    for record in reversed(term_records):
                        target = record["to"]
                        if target.casefold() in conflicting_targets:
                            continue
                        index = next(
                            (
                                item
                                for item, term in enumerate(terms)
                                if term == target
                            ),
                            None,
                        )
                        if index is None:
                            continue
                        if record["action"] == "added":
                            terms.pop(index)
                        elif record["action"] == "recased":
                            terms[index] = str(record["before"])

                try:
                    self._transact_settings(rollback_learning)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "message": f"Could not save correction history: {exc}",
                    "changes": [],
                    "session_id": None,
                }

            return {
                "ok": True,
                "message": (
                    f"Learned {len(applied_changes)} correction"
                    f"{'s' if len(applied_changes) != 1 else ''}."
                ),
                "changes": applied_changes,
                "session_id": session_id,
                "rejected": preview.get("rejected", []),
            }

    def undo_last(self) -> dict[str, Any]:
        """Serialize one complete settings/history undo transaction."""

        with self._lock:
            try:
                with self._store.transaction():
                    return self._undo_last_locked()
            except (OSError, TimeoutError) as exc:
                self._storage_error = str(exc)
                return {
                    "ok": False,
                    "message": f"Correction history is unavailable: {exc}",
                    "changes": [],
                    "session_id": None,
                }

    def _undo_last_locked(self) -> dict[str, Any]:
        """Undo the latest active session without overwriting newer edits."""

        with self._lock:
            try:
                state = self._load_state()
            except (ValueError, OSError) as exc:
                self._storage_error = str(exc)
                return {
                    "ok": False,
                    "message": f"Correction history is unavailable: {exc}",
                    "changes": [],
                    "session_id": None,
                }
            session = next(
                (item for item in reversed(state["sessions"]) if item.get("undone") is None),
                None,
            )
            if session is None:
                return {
                    "ok": False,
                    "message": "There is no learned correction to undo.",
                    "changes": [],
                    "session_id": None,
                }

            def apply_undo(
                vocabulary: dict[str, str], terms: list[str]
            ) -> dict[str, Any]:
                before_terms = list(terms)
                reverted: list[dict[str, Any]] = []
                skipped: list[dict[str, str]] = []
                conflicting_targets: set[str] = set()
                mapping_rollbacks: list[dict[str, Any]] = []

                for record in reversed(session["changes"]):
                    key = str(record.get("key", ""))
                    applied_target = str(record.get("to", ""))
                    if not key or vocabulary.get(key) != applied_target:
                        skipped.append(
                            {"from": key, "reason": "mapping_changed_later"}
                        )
                        conflicting_targets.add(applied_target.casefold())
                        continue
                    if bool(record.get("before_present")):
                        restored = str(record.get("before", ""))
                        vocabulary[key] = restored
                        mapping_rollbacks.append(
                            {
                                "key": key,
                                "to": applied_target,
                                "after_present": True,
                                "after": restored,
                            }
                        )
                    else:
                        del vocabulary[key]
                        mapping_rollbacks.append(
                            {
                                "key": key,
                                "to": applied_target,
                                "after_present": False,
                                "after": None,
                            }
                        )
                    reverted.append({"from": key, "to": applied_target})

                # A changed term-list revision means another process edited it
                # after learning. Preserve that entire newer collection.
                terms_unchanged = (
                    _json_hash(terms) == session.get("terms_after_hash")
                )
                if terms_unchanged:
                    for record in reversed(session["term_changes"]):
                        target = str(record.get("to", ""))
                        if target.casefold() in conflicting_targets:
                            skipped.append(
                                {
                                    "from": target,
                                    "reason": "related_mapping_changed_later",
                                }
                            )
                            continue
                        index = next(
                            (
                                item
                                for item, term in enumerate(terms)
                                if term == target
                            ),
                            None,
                        )
                        if index is None:
                            skipped.append(
                                {"from": target, "reason": "term_changed_later"}
                            )
                        elif record.get("action") == "added":
                            terms.pop(index)
                        elif record.get("action") == "recased":
                            terms[index] = str(record.get("before", target))
                elif session["term_changes"]:
                    skipped.append(
                        {
                            "from": "corrected_terms",
                            "reason": "terms_changed_later",
                        }
                    )

                terms_after = list(terms)
                return {
                    "reverted": reverted,
                    "skipped": skipped,
                    "mapping_rollbacks": mapping_rollbacks,
                    "terms_before": before_terms,
                    "terms_after": terms_after,
                    "settings_changed": bool(mapping_rollbacks)
                    or terms_after != before_terms,
                }

            try:
                transaction = self._transact_settings(apply_undo)
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"Could not update settings: {exc}",
                    "changes": [],
                    "session_id": session["id"],
                }

            reverted = transaction["reverted"]
            skipped = transaction["skipped"]
            settings_changed = transaction["settings_changed"]

            session["undone"] = {
                "at": _utc_now(),
                "reverted_mapping_count": len(reverted),
                "skipped_count": len(skipped),
            }
            try:
                self._store.save(state)
            except (OSError, TypeError, ValueError) as exc:
                self._storage_error = str(exc)
                if settings_changed:
                    mapping_rollbacks = transaction["mapping_rollbacks"]
                    terms_before = transaction["terms_before"]
                    terms_after = transaction["terms_after"]

                    def rollback_undo(
                        vocabulary: dict[str, str], terms: list[str]
                    ) -> None:
                        for record in mapping_rollbacks:
                            key = record["key"]
                            if record["after_present"]:
                                if vocabulary.get(key) == record["after"]:
                                    vocabulary[key] = record["to"]
                            elif key not in vocabulary:
                                vocabulary[key] = record["to"]
                        if _json_hash(terms) == _json_hash(terms_after):
                            terms[:] = terms_before

                    try:
                        self._transact_settings(rollback_undo)
                    except Exception:
                        pass
                return {
                    "ok": False,
                    "message": f"Could not save undo history: {exc}",
                    "changes": [],
                    "session_id": session["id"],
                }

            return {
                "ok": True,
                "message": (
                    "Correction undone."
                    if not skipped
                    else "Undo completed; later edits were preserved."
                ),
                "changes": reverted,
                "skipped": skipped,
                "session_id": session["id"],
            }

    def status(self) -> dict[str, Any]:
        """Return availability, opt-in state, and transcript-free history counts."""

        with self._lock:
            try:
                enabled = bool(self._get(self.ENABLED_SETTING, False))
            except Exception:
                enabled = False
            try:
                state = self._load_state()
                active = [item for item in state["sessions"] if item.get("undone") is None]
                last = active[-1]["id"] if active else None
                last_summary = (
                    {
                        "id": active[-1]["id"],
                        "changes": [
                            {"from": change["key"], "to": change["to"]}
                            for change in active[-1]["changes"]
                        ],
                    }
                    if active
                    else None
                )
                learned_count = sum(len(item["changes"]) for item in active)
            except (ValueError, OSError) as exc:
                self._storage_error = str(exc)
                active = []
                last = None
                last_summary = None
                learned_count = 0
            return {
                "enabled": enabled,
                "ready": self._storage_error is None,
                "undo_available": bool(active),
                "active_session_count": len(active),
                "learned_count": learned_count,
                "history_count": len(state["sessions"]) if self._storage_error is None else 0,
                "last_session_id": last,
                "last": last_summary,
                "storage_error": self._storage_error,
                "audit_path": str(self._store.path),
            }


__all__ = ["CorrectionLearningManager"]
