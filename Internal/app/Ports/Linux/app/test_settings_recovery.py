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
    check("recovery immediately heals the live settings file",
          json.load(open(SP(), encoding="utf-8")).get("cerebras_api_key")
          == "csk-GOOD-KEY")
    check("no crash when recovering with a good .bak", True)

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

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
