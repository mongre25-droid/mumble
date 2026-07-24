#!/usr/bin/env python3
"""Cross-process settings merge — regression test for the API-key clobber bug.

The controller and the web window are SEPARATE processes sharing one
settings.json. Before the read-merge fix, a process that saved its whole
in-memory snapshot would wipe a key another process had written but it never
saw — the "API key works, then drops after a few hours" bug. These tests pin
the fix: a save overwrites only the keys THAT process changed; every other key
adopts the latest on-disk value.

Run: python test_settings_merge.py   (offline, no deps)
"""
import json
import os
import tempfile
from contextlib import contextmanager

import branding


def _fresh_tmp():
    d = tempfile.mkdtemp(prefix="mumble_set_")
    branding.DATA_DIR = d
    branding.SETTINGS_PATH = os.path.join(d, "settings.json")
    return d


def _reimport_settings():
    # settings caches branding paths only at call time, so a plain import is fine.
    import importlib
    import settings as _s
    importlib.reload(_s)
    return _s


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok  ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


def main():
    _fresh_tmp()
    settings = _reimport_settings()

    print("== VAL-SET-001: a stale process never clobbers another's key ==")
    # Two long-lived processes, each created before the other's edits.
    a = settings.Settings()
    b = settings.Settings()  # stale "controller": blank cerebras_api_key in memory

    a.set("cerebras_api_key", "csk-SECRET-123")  # web UI saves the key to disk
    b.set("model", "medium.en")                  # controller saves a DIFFERENT key

    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("API key entered by process A survives process B's save",
          disk.get("cerebras_api_key") == "csk-SECRET-123")
    check("process B's own change (model) is persisted",
          disk.get("model") == "medium.en")

    # A reload (a brand-new process) sees both.
    c = settings.Settings()
    check("fresh load still has the key", c.get("cerebras_api_key") == "csk-SECRET-123")
    check("fresh load still has model", c.get("model") == "medium.en")

    print("== VAL-SET-002: the saving process adopts the other's on-disk change ==")
    # b changed model to medium.en on disk; a (stale) now saves a third key.
    a.set("user_name", "Joseph")
    check("a's save did not revert b's model change",
          json.load(open(branding.SETTINGS_PATH, encoding="utf-8")).get("model") == "medium.en")
    check("a in-memory converged to disk model after save", a.get("model") == "medium.en")
    check("a's own new key persisted", a.get("user_name") == "Joseph")

    print("== VAL-SET-003: factory reset still overwrites everything ==")
    a.reset_all()
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("reset cleared the API key", disk.get("cerebras_api_key") == "")
    check("reset cleared the model back to default", disk.get("model") == settings.DEFAULTS["model"])

    print("== VAL-SET-004: the lock helper round-trips without error ==")
    with settings.exclusive_file_lock(branding.SETTINGS_PATH) as acquired:
        check("lock acquired", acquired)
    with settings.exclusive_file_lock(branding.SETTINGS_PATH) as reacquired:
        check("lock can be reacquired after release", reacquired)

    print("== VAL-SET-005: lock contention fails closed ==")
    before = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))

    @contextmanager
    def unavailable_lock(*_args, **_kwargs):
        yield False

    original_lock = settings.exclusive_file_lock
    settings.exclusive_file_lock = unavailable_lock
    try:
        check("set reports an unsaved change", a.set("user_name", "Not saved") is False)
    finally:
        settings.exclusive_file_lock = original_lock
    after = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("contention never writes without the lock", after == before)

    print("== VAL-SET-006: retired concepts cannot return from disk ==")
    stale = dict(after)
    stale.update({
        "prompt_memory": True,
        "prompt_provider": "openrouter",
        "hardware_tier": "powerful",
        "hardware_tier_resolved": "powerful",
        "llm_provider": "anthropic",
    })
    with open(branding.SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(stale, handle)
    migrated = settings.Settings()
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("retired settings removed from memory",
          not settings.REMOVED_SETTINGS.intersection(migrated.data))
    check("retired settings tombstoned on disk",
          not settings.REMOVED_SETTINGS.intersection(disk))
    check("retired processing provider heals to Cerebras",
          disk.get("llm_provider") == "cerebras")

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
