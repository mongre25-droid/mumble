#!/usr/bin/env python3
"""VAL-RECV-002: Startup with a corrupted settings file.

If settings.json is corrupted or unparseable, the app MUST recover by loading
safe defaults without crashing, back up the corrupted file, and notify the user
of the recovery. All core settings revert to defaults so dictation, tray, and
hotkeys keep working.

This test pins the recovery path in settings.Settings.load():
  - corrupt settings.json (invalid JSON) -> no crash, safe defaults loaded
  - the corrupted file is preserved as a backup (settings.json.bak or .corrupt)
  - a recovery notification is emitted
  - when a prior good .bak exists, recovery loads the last-good values instead
  - core defaults (model, language, modes) are sane after recovery

Run: python test_settings_recovery.py   (offline, no deps)
"""
import io
import json
import os
import tempfile

import branding


def _fresh_tmp():
    d = tempfile.mkdtemp(prefix="mumble_recv_")
    branding.DATA_DIR = d
    branding.SETTINGS_PATH = os.path.join(d, "settings.json")
    return d


def _reimport_settings():
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


def _capture_print(fn):
    """Run fn() and return everything printed to stdout during it."""
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def main():
    settings = _reimport_settings()
    SP = lambda: branding.SETTINGS_PATH

    print("== RECV-002a: corrupt settings.json, no prior .bak -> safe defaults ==")
    d = _fresh_tmp()
    # Write a corrupted settings file (invalid JSON — a truncated/garbled file).
    with open(SP(), "w", encoding="utf-8") as f:
        f.write('{"cerebras_api_key": "sk-SECRET", "model": "small.en",')  # truncated
    assert os.path.exists(SP())

    out = _capture_print(lambda: settings.Settings())
    s = settings.Settings  # noqa: F841  (instance created inside capture)
    # Re-create fresh to inspect state (the captured one is fine, but reload to
    # be deterministic about on-disk state).
    inst = settings.Settings()
    check("no crash on corrupted settings", True)
    check("defaults loaded — model is the default",
          inst.get("model") == settings.DEFAULTS["model"])
    check("defaults loaded — cerebras_api_key blank",
          inst.get("cerebras_api_key") == settings.DEFAULTS["cerebras_api_key"])
    # The corrupted file must be preserved as a backup.
    bak = SP() + ".bak"
    corrupt = SP() + ".corrupt"
    check("corrupted file preserved as a backup (.bak or .corrupt)",
          os.path.exists(bak) or os.path.exists(corrupt))
    # A recovery notification must be emitted.
    check("recovery notification emitted",
          "corrupt" in out.lower() or "recover" in out.lower()
          or "load error" in out.lower() or "backup" in out.lower())

    print("== RECV-002b: corrupt settings.json WITH a prior good .bak -> recover last-good ==")
    d = _fresh_tmp()
    # First, write a GOOD settings file and let a save produce a good .bak.
    good = settings.Settings()
    good.set("cerebras_api_key", "csk-GOOD-KEY")
    # After save, a .bak of the good file should exist.
    check("good .bak exists after a successful save",
          os.path.exists(SP() + ".bak"))
    good_bak = SP() + ".bak"
    check("good .bak contains the good key",
          json.load(open(good_bak, encoding="utf-8")).get("cerebras_api_key") == "csk-GOOD-KEY")
    # Now corrupt the live settings.json.
    with open(SP(), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json at all {{{ ,,,")
    # Reload — should recover the good key from .bak, not crash.
    inst2 = settings.Settings()
    check("recovered last-good API key from .bak after corruption",
          inst2.get("cerebras_api_key") == "csk-GOOD-KEY")
    check("no crash when recovering with a good .bak", True)
    # Recovery must also heal the primary immediately.  Otherwise an install
    # whose migration guards are already current repeats the same recovery on
    # every launch until the user happens to change another setting.
    healed = json.load(open(SP(), encoding="utf-8"))
    check("recovery atomically heals the live settings file",
          healed.get("cerebras_api_key") == "csk-GOOD-KEY")
    check("recovery leaves no temporary restore file",
          not os.path.exists(SP() + ".recover.tmp"))

    print("== RECV-002c: corrupted file content is preserved for inspection ==")
    # The user (or a developer) should be able to inspect what went wrong.
    # At least one backup artifact must contain the corrupted bytes.
    d = _fresh_tmp()
    garbage = "{\n  \"broken\": <<<not json>>>\n  trailing garbage,,,\n"
    with open(SP(), "w", encoding="utf-8") as f:
        f.write(garbage)
    settings.Settings()
    preserved = False
    for suffix in (".bak", ".corrupt"):
        p = SP() + suffix
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    if "broken" in fh.read():
                        preserved = True
            except Exception:
                pass
    check("corrupted bytes preserved in a backup artifact", preserved)

    print("== RECV-002d: core defaults are sane after recovery (dictation/tray/hotkeys) ==")
    d = _fresh_tmp()
    with open(SP(), "w", encoding="utf-8") as f:
        f.write("not json")
    inst3 = settings.Settings()
    check("default model present", bool(inst3.get("model")))
    check("default language present", bool(inst3.get("language")))
    check("default modes dict present",
          isinstance(inst3.get("modes"), dict) and len(inst3.get("modes")) > 0)
    check("default english_only present", "english_only" in inst3.data)
    check("recovered Search shortcut uses the Find default",
          inst3.get("search_hotkey") == "ctrl+alt+f")

    print("== RECV-002e: non-object and unsafe values use recovery ==")
    _fresh_tmp()
    with open(SP(), "w", encoding="utf-8") as f:
        json.dump(["not", "a", "settings", "object"], f)
    non_object = settings.Settings()
    check("valid JSON with a non-object root is treated as damaged",
          non_object.get("model") == settings.DEFAULTS["model"])
    check("non-object recovery is visible to the Settings UI",
          bool(non_object.recovery_notice))

    _fresh_tmp()
    unsafe = dict(settings.DEFAULTS)
    unsafe["history_max"] = -1
    with open(SP(), "w", encoding="utf-8") as f:
        json.dump(unsafe, f)
    invalid_value = settings.Settings()
    check("startup-unsafe negative history limit is quarantined",
          invalid_value.get("history_max") == settings.DEFAULTS["history_max"])
    check("unsafe source bytes are preserved",
          os.path.exists(SP() + ".corrupt"))

    print("== RECV-002f: a live stale writer fails closed on corruption ==")
    _fresh_tmp()
    stale = settings.Settings()
    stale.set("cerebras_api_key", "csk-STILL-GOOD")
    good_backup = open(SP() + ".bak", "rb").read()
    corrupt_bytes = b'{"user_name": "truncated"'
    with open(SP(), "wb") as f:
        f.write(corrupt_bytes)
    check("save against newly corrupt primary is refused",
          stale.set("user_name", "Must remain pending") is False)
    check("corrupt primary bytes are left untouched",
          open(SP(), "rb").read() == corrupt_bytes)
    check("last-good backup is left untouched",
          open(SP() + ".bak", "rb").read() == good_backup)
    check("refused value remains dirty for explicit recovery/retry",
          "user_name" in stale._dirty and stale.get("user_name") == "Must remain pending")

    _fresh_tmp()
    unsafe_stale = settings.Settings()
    unsafe_stale.set("cerebras_api_key", "fake-good-key")
    unsafe_backup = open(SP() + ".bak", "rb").read()
    unsafe_primary = json.load(open(SP(), encoding="utf-8"))
    unsafe_primary["history_max"] = -1
    with open(SP(), "w", encoding="utf-8") as handle:
        json.dump(unsafe_primary, handle)
    check("stale writer also refuses structurally unsafe live values",
          unsafe_stale.set("user_name", "Keep pending") is False)
    check("unsafe live JSON is not laundered into a new generation",
          json.load(open(SP(), encoding="utf-8"))["history_max"] == -1)
    check("unsafe live JSON cannot replace the good backup",
          open(SP() + ".bak", "rb").read() == unsafe_backup)

    print("== RECV-002g: write and backup failures are reported and retryable ==")
    _fresh_tmp()
    durable = settings.Settings()
    old_name = durable.get("user_name")
    original_fsync = settings.os.fsync
    settings.os.fsync = lambda _fd: (_ for _ in ()).throw(OSError("fsync injected"))
    try:
        fsync_result = durable.set("user_name", "Pending fsync")
    finally:
        settings.os.fsync = original_fsync
    check("fsync failure is reported", fsync_result is False)
    check("fsync failure leaves the value dirty", "user_name" in durable._dirty)
    check("fsync failure does not replace the primary",
          json.load(open(SP(), encoding="utf-8")).get("user_name") == old_name)
    check("retry after fsync failure succeeds", durable.save() is True)
    check("retried value reaches disk",
          json.load(open(SP(), encoding="utf-8")).get("user_name") == "Pending fsync")

    original_copy = settings.shutil.copyfileobj
    before_backup_failure = json.load(open(SP(), encoding="utf-8"))
    settings.shutil.copyfileobj = lambda *_a, **_k: (
        (_ for _ in ()).throw(OSError("backup copy injected"))
    )
    try:
        backup_result = durable.set("user_name", "Pending backup")
    finally:
        settings.shutil.copyfileobj = original_copy
    check("backup write failure is reported", backup_result is False)
    check("backup failure keeps the value dirty for retry",
          "user_name" in durable._dirty)
    check("reported backup failure rolls the primary back",
          json.load(open(SP(), encoding="utf-8")) == before_backup_failure)
    check("retry after backup failure succeeds", durable.save() is True)
    check("backup retry converges primary and backup",
          json.load(open(SP(), encoding="utf-8")) ==
          json.load(open(SP() + ".bak", encoding="utf-8")))

    print("== RECV-002h: a missing primary recovers before migration writes ==")
    _fresh_tmp()
    recoverable = settings.Settings()
    recoverable.set("cerebras_api_key", "fake-backup-key")
    os.remove(SP())
    restored = settings.Settings()
    check("missing primary restores the last-good API key",
          restored.get("cerebras_api_key") == "fake-backup-key")
    check("missing primary is recreated atomically", os.path.exists(SP()))
    check("missing-file recovery is visible", bool(restored.recovery_notice))

    _fresh_tmp()
    with open(SP() + ".bak", "w", encoding="utf-8") as handle:
        handle.write("not usable json")
    fallback = settings.Settings()
    check("unusable backup falls back to safe defaults",
          fallback.get("model") == settings.DEFAULTS["model"])
    check("unusable backup bytes are preserved separately",
          os.path.exists(SP() + ".bak.corrupt"))
    check("unusable-backup fallback is visible", bool(fallback.recovery_notice))

    _fresh_tmp()
    reset_failure = settings.Settings()
    reset_failure.set("user_name", "Keep me")
    original_reset_fsync = settings.os.fsync
    settings.os.fsync = lambda _fd: (
        (_ for _ in ()).throw(OSError("reset fsync injected"))
    )
    try:
        reset_result = reset_failure.reset_all()
    finally:
        settings.os.fsync = original_reset_fsync
    check("failed reset is reported", reset_result is False)
    check("failed reset keeps the in-memory configuration",
          reset_failure.get("user_name") == "Keep me")
    check("failed reset keeps the on-disk configuration",
          json.load(open(SP(), encoding="utf-8"))["user_name"] == "Keep me")

    if os.name != "nt":
        private_modes = [
            os.stat(path).st_mode & 0o777
            for path in (SP(), SP() + ".bak") if os.path.exists(path)
        ]
        check("primary and backup are owner-private",
              bool(private_modes) and all(mode & 0o077 == 0 for mode in private_modes))

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
