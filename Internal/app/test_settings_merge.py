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
import threading

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
    fd = settings._acquire_lock()
    check("lock acquired", fd is not None)
    settings._release_lock(fd)
    check("lock file removed after release", not os.path.exists(settings._lock_path()))

    print("== VAL-SET-005: vocabulary transaction merges stale collection state ==")
    manual = settings.Settings()
    learner = settings.Settings()  # intentionally stale after manual's next save
    manual.update(
        vocabulary={"manual heard": "ManualTerm"},
        vocabulary_terms=["ManualTerm"],
    )

    def learn_from_latest(vocabulary, terms):
        vocabulary["mum bull"] = "Mumble"
        terms.append("Mumble")

    learner.atomic_vocabulary_update(learn_from_latest)
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("manual mapping survives stale learner transaction",
          disk.get("vocabulary", {}).get("manual heard") == "ManualTerm")
    check("learned mapping is added beside manual mapping",
          disk.get("vocabulary", {}).get("mum bull") == "Mumble")
    check("manual and learned corrected terms both survive",
          set(disk.get("vocabulary_terms", [])) == {"ManualTerm", "Mumble"})
    check("stale learner memory converges to merged disk collections",
          learner.get("vocabulary", {}).get("manual heard") == "ManualTerm")

    print("== VAL-SET-006: stale writers cannot resurrect reset/deleted keys ==")
    stale = settings.Settings()
    extension = settings.Settings()
    extension.set("future_extension_config", {"enabled": True})
    resetter = settings.Settings()
    resetter.reset_all()
    stale.set("user_name", "After reset")
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("unknown key deleted by reset stays deleted",
          "future_extension_config" not in disk)
    check("stale writer's explicit post-reset edit still lands",
          disk.get("user_name") == "After reset")

    print("== VAL-SET-007: different children merge atomically ==")
    first = settings.Settings()
    second = settings.Settings()
    first.atomic_mapping_update(
        "prompt_prefs", lambda prefs: prefs.__setitem__("tone", "Casual")
    )
    second.atomic_mapping_update(
        "prompt_prefs", lambda prefs: prefs.__setitem__("detail", "Comprehensive")
    )
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("first prompt preference survives second process",
          disk["prompt_prefs"]["tone"] == "Casual")
    check("second prompt preference lands beside first",
          disk["prompt_prefs"]["detail"] == "Comprehensive")

    print("== VAL-SET-008: failed dirty child is preserved on atomic retry ==")
    pending = settings.Settings()
    concurrent = settings.Settings()
    original_write = pending._write_atomic
    pending._write_atomic = lambda: False
    pending_prefs = dict(pending.get("prompt_prefs"))
    pending_prefs["tone"] = "Friendly"
    check("injected prompt-preference failure is reported",
          pending.set("prompt_prefs", pending_prefs) is False)
    pending._write_atomic = original_write
    concurrent.atomic_mapping_update(
        "prompt_prefs", lambda prefs: prefs.__setitem__("detail", "Brief")
    )
    pending.atomic_mapping_update(
        "prompt_prefs", lambda prefs: prefs.__setitem__("structure", "Prose")
    )
    disk_prefs = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))["prompt_prefs"]
    check("previously failed child reaches disk on retry",
          disk_prefs["tone"] == "Friendly")
    check("concurrent child is not clobbered by retry",
          disk_prefs["detail"] == "Brief")
    check("new child from retry also reaches disk",
          disk_prefs["structure"] == "Prose")

    print("== VAL-SET-009: failed vocabulary values survive learner update ==")
    pending_vocab = settings.Settings()
    concurrent_vocab = settings.Settings()
    original_write = pending_vocab._write_atomic
    pending_vocab._write_atomic = lambda: False
    check("injected vocabulary save failure is reported",
          pending_vocab.update(
              vocabulary={"pending heard": "PendingTerm"},
              vocabulary_terms=["PendingTerm"],
          ) is False)
    pending_vocab._write_atomic = original_write
    concurrent_vocab.atomic_vocabulary_update(
        lambda vocabulary, terms: (
            vocabulary.__setitem__("other heard", "OtherTerm"),
            terms.append("OtherTerm"),
        )
    )
    pending_vocab.atomic_vocabulary_update(
        lambda vocabulary, terms: (
            vocabulary.__setitem__("new heard", "NewTerm"),
            terms.append("NewTerm"),
        )
    )
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("pending and concurrent vocabulary mappings survive",
          set(disk["vocabulary"].values()) >= {"PendingTerm", "OtherTerm", "NewTerm"})
    check("pending and concurrent vocabulary terms survive",
          set(disk["vocabulary_terms"]) >= {"PendingTerm", "OtherTerm", "NewTerm"})

    print("== VAL-SET-010: custom nested configuration round-trips ==")
    author = settings.Settings()
    author.update(
        prompt_keywords={"prompt": "default", "custom-workflow": "Keep me"},
        prompt_prefs={"tone": "Neutral", "future-pref": {"enabled": True}},
        modes={"prompt": True, "future-mode": False},
    )
    reader = settings.Settings()
    reader.atomic_mapping_update(
        "prompt_prefs", lambda prefs: prefs.__setitem__("detail", "Balanced")
    )
    disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
    check("custom prompt keyword survives load and unrelated nested save",
          disk["prompt_keywords"]["custom-workflow"] == "Keep me")
    check("future prompt preference survives load and nested save",
          disk["prompt_prefs"]["future-pref"] == {"enabled": True})
    check("future mode child survives load", disk["modes"]["future-mode"] is False)

    print("== VAL-SET-011: invalid writes do not mutate memory or disk ==")
    guarded = settings.Settings()
    before_memory = guarded.get("history_max")
    before_disk = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))["history_max"]
    check("negative history size is rejected", guarded.set("history_max", -1) is False)
    check("invalid history size does not mutate memory",
          guarded.get("history_max") == before_memory)
    check("invalid history size does not mutate disk",
          json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))["history_max"] == before_disk)
    check("unsupported interactive provider is rejected",
          guarded.set("llm_provider", "unreviewed-cloud") is False)
    original_modes = dict(guarded.get("modes"))
    check("invalid nested mode values are rejected",
          guarded.set("modes", {"prompt": {"not": "a boolean"}}) is False)
    check("invalid nested mode values do not mutate memory",
          guarded.get("modes") == original_modes)
    check("non-serialisable known mappings are rejected",
          guarded.set("prompt_prefs", {"tone": object()}) is False)
    before_atomic_modes = json.load(
        open(branding.SETTINGS_PATH, encoding="utf-8")
    )["modes"]
    try:
        guarded.atomic_mapping_update(
            "modes", lambda modes: modes.__setitem__("bad", {"not": "boolean"})
        )
        invalid_atomic_rejected = False
    except ValueError:
        invalid_atomic_rejected = True
    check("atomic nested writes use the same validation boundary",
          invalid_atomic_rejected)
    check("rejected atomic nested write leaves disk unchanged",
          json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))[
              "modes"] == before_atomic_modes)

    print("== VAL-SET-012: load and set share one process lock ==")
    serialised = settings.Settings()
    load_entered = threading.Event()
    allow_load = threading.Event()
    set_finished = threading.Event()
    original_load = serialised._load_unlocked

    def blocked_load():
        load_entered.set()
        if not allow_load.wait(2):
            raise TimeoutError("test did not release the blocked load")
        return original_load()

    serialised._load_unlocked = blocked_load
    load_thread = threading.Thread(target=serialised.load)

    def save_after_load():
        serialised.set("user_name", "Serialized writer")
        set_finished.set()

    set_thread = threading.Thread(target=save_after_load)
    load_thread.start()
    check("load reached the protected disk-read section", load_entered.wait(1))
    set_thread.start()
    check("setter waits while load holds the process lock",
          not set_finished.wait(0.05))
    allow_load.set()
    load_thread.join(2)
    set_thread.join(2)
    check("load and setter both finish after lock release",
          not load_thread.is_alive() and not set_thread.is_alive())
    check("the queued setter reaches disk after the load",
          json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))[
              "user_name"] == "Serialized writer")

    print("== VAL-SET-013: an old lock owner cannot remove its replacement ==")
    first_fd = settings._acquire_lock()
    lock_path = settings._lock_path()
    # Windows does not allow unlinking a file while this process holds it open.
    # Closing the displaced descriptor first gives _release_lock the same stale,
    # non-owning token that an unlinked descriptor represents on POSIX.
    if os.name == "nt":
        os.close(first_fd)
        first_fd = -1
    os.remove(lock_path)
    replacement_fd = settings._acquire_lock()
    settings._release_lock(first_fd)
    check("releasing the displaced owner keeps the replacement lock",
          os.path.exists(lock_path))
    settings._release_lock(replacement_fd)
    check("the replacement owner can release its own lock",
          not os.path.exists(lock_path))

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
