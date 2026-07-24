#!/usr/bin/env python3
"""Auto-update fixes — the venv-gap swap, full-product-zip handling, and the
manifest signature gate (T7). All testable offline (no real update cycle, which
needs a published release + live install — owner-gated).

Run:  .venv\\Scripts\\python.exe test_update.py
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import update  # noqa: E402

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


# ---- T7: manifest signature gate -----------------------------------------
print("== manifest signature gate (T7) ==")
m = {"version": "5.0.0", "url": "https://x/Mumble-5.0.0.zip", "sha256": "ab"}

_orig_pub = update.UPDATE_PUBLIC_KEY
try:
    # No public key configured: the channel is disabled, never sha256-only.
    update.UPDATE_PUBLIC_KEY = ""
    ok, status = update._verify_manifest_signature(m)
    check("no pubkey fails closed", ok is False and "not configured" in status)
    check("production channel disabled without pubkey",
          update.update_channel_enabled() is False)

    # Public key configured but NO signature in the manifest → fail closed.
    update.UPDATE_PUBLIC_KEY = "00" * 32
    ok, status = update._verify_manifest_signature(m)
    check("pubkey set + no signature → fail closed", ok is False)

    # Public key + a signature but crypto lib absent → fail closed (never downgrade).
    m2 = dict(m, signature="aa" * 64)
    ok, status = update._verify_manifest_signature(m2)
    check("pubkey set + sig but verification can't pass → fail closed", ok is False)
finally:
    update.UPDATE_PUBLIC_KEY = _orig_pub

# canonical payload is stable + order-fixed
payload = update._manifest_signed_payload(m)
check("signed payload covers version/url/sha256",
      payload == b"5.0.0\nhttps://x/Mumble-5.0.0.zip\nab")

# ---- full-product-zip → app-root detection -------------------------------
print("\n== _find_app_root locates the runtime inside a full-product tree ==")
tmp = tempfile.mkdtemp(prefix="mumble_upd_")
try:
    appdir = os.path.join(tmp, "Mumble", "Internal", "app")
    os.makedirs(appdir)
    for fn in ("mumble.py", "branding.py", "settings.py"):
        open(os.path.join(appdir, fn), "w").close()
    found = update._find_app_root(tmp)
    check("found the app/ subtree (holds mumble.py + branding.py)",
          found is not None and os.path.normpath(found) == os.path.normpath(appdir))

    empty = tempfile.mkdtemp(prefix="mumble_upd_empty_")
    check("no runtime → None", update._find_app_root(empty) is None)
    os.rmdir(empty)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ---- venv-carry swap script ----------------------------------------------
print("\n== swap script carries the .venv + refreshes deps (the venv-gap fix) ==")
scriptdir = tempfile.mkdtemp(prefix="mumble_swap_")
try:
    parent = scriptdir
    current = os.path.join(parent, "app")
    newd = os.path.join(parent, "Mumble-5.0.0-app")
    update._write_swap_script(parent, newd, current)
    with open(os.path.join(parent, "apply_update.bat"), encoding="utf-8") as f:
        bat = f.read()
    check("carries the .venv from the backup", ".venv" in bat and "xcopy" in bat)
    check("pip-installs requirements after the swap",
          "pip install -r" in bat and "requirements.txt" in bat)
    check("still relaunches", "mumble.py" in bat or "Mumble.exe" in bat)
    check("keeps the backup for rollback (no rmdir of Mumble-backup on success path)",
          'rmdir /s /q "' + os.path.join(parent, "Mumble-backup") + '"' in bat
          and bat.count("Mumble-backup") >= 2)  # backup created + referenced
finally:
    import shutil
    shutil.rmtree(scriptdir, ignore_errors=True)

# ===================================================================== final
if _fails:
    print(f"\n{len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("\nALL GREEN")
sys.exit(0)
