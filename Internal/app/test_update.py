#!/usr/bin/env python3
"""Auto-update fixes — the venv-gap swap, full-product-zip handling, and the
manifest signature gate (T7). All testable offline (no real update cycle, which
needs a published release + live install — owner-gated).

Run:  .venv\\Scripts\\python.exe test_update.py
"""
import os
import shutil
import subprocess
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


def make_product(path, label):
    """Create the smallest complete product tree accepted by the updater."""
    app_dir = os.path.join(path, "Internal", "app")
    os.makedirs(app_dir)
    for filename in ("Mumble.exe", "LICENSE"):
        with open(os.path.join(path, filename), "w", encoding="utf-8") as handle:
            handle.write(label)
    for filename in ("mumble.py", "branding.py", "install.ps1", "uninstall.ps1"):
        with open(os.path.join(app_dir, filename), "w", encoding="utf-8") as handle:
            handle.write(label)
    return app_dir


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

# ---- full-product-zip -> product-root detection -----------------------------
print("\n== _find_product_root retains the complete release tree ==")
tmp = tempfile.mkdtemp(prefix="mumble_upd_")
try:
    product = os.path.join(tmp, "Mumble")
    appdir = os.path.join(product, "Internal", "app")
    os.makedirs(appdir)
    for fn in ("Mumble.exe", "LICENSE"):
        open(os.path.join(product, fn), "w").close()
    for fn in ("mumble.py", "branding.py", "settings.py",
               "install.ps1", "uninstall.ps1"):
        open(os.path.join(appdir, fn), "w").close()
    found = update._find_product_root(tmp)
    check("found the canonical product root, not only Internal/app",
          found is not None and os.path.normpath(found) == os.path.normpath(product))
    check("root launcher remains inside the staged update",
          bool(found and os.path.isfile(os.path.join(found, "Mumble.exe"))))
    check("installed app resolves to its complete product root",
          os.path.normpath(update._installed_product_root(appdir))
          == os.path.normpath(product))
    sibling_product = os.path.join(tmp, "Mumble Sibling")
    sibling_app = make_product(sibling_product, "sibling")
    first_script = update.pending_update_script(appdir)
    sibling_script = update.pending_update_script(sibling_app)
    check("sibling installs have private pending-update scripts",
          first_script and sibling_script and first_script != sibling_script
          and os.path.commonpath([first_script, tmp]) == os.path.normpath(tmp)
          and os.path.commonpath([sibling_script, tmp]) == os.path.normpath(tmp))

    empty = tempfile.mkdtemp(prefix="mumble_upd_empty_")
    check("no complete product -> None", update._find_product_root(empty) is None)
    os.rmdir(empty)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ---- venv-carry swap script ----------------------------------------------
print("\n== swap script carries the .venv + refreshes deps (the venv-gap fix) ==")
scriptdir = tempfile.mkdtemp(prefix="mumble_swap_")
try:
    parent = scriptdir
    current = os.path.join(parent, "Mumble")
    newd = os.path.join(parent, "Mumble-5.0.0-product")
    make_product(current, "current")
    make_product(newd, "new")
    old_backup = os.path.join(parent, "Mumble-backup")
    os.makedirs(old_backup)
    sentinel = os.path.join(old_backup, "keep-me.txt")
    open(sentinel, "w", encoding="utf-8").close()
    script_path = update._write_swap_script(
        parent, newd, current, startup_enabled=False)
    with open(script_path, encoding="utf-8") as f:
        bat = f.read()
    check("pending script is private to this installation",
          os.path.normpath(script_path)
          == os.path.normpath(update.pending_update_script(
              os.path.join(current, "Internal", "app"))))
    check("carries the app .venv from the backup",
          "Internal\\app\\.venv" in bat and "xcopy" in bat)
    check("pip-installs requirements after the swap",
          "pip install -r" in bat and "requirements.txt" in bat)
    check("relaunches from the updated complete product",
          "Internal\\app\\mumble.py" in bat and "Mumble.exe" in bat)
    check("prior rollback sentinel is never selected for deletion",
          os.path.exists(sentinel)
          and ('rmdir /s /q "' + old_backup + '"') not in bat)
    check("swap uses a collision-safe transaction-owned backup",
          "Mumble-backup-" in bat and ".mumble-update-owner.json" in bat)
    check("disabled run-at-login choice is preserved during update",
          "autostart.set_enabled(False)" in bat
          and "autostart.enable()" not in bat)
finally:
    shutil.rmtree(scriptdir, ignore_errors=True)

# ---- rollback ownership, collision safety, and startup choice ------------
print("\n== rollback touches only its owned transaction ==")
rollback_dir = tempfile.mkdtemp(prefix="Mumble rollback path with spaces ")
try:
    current = os.path.join(rollback_dir, "Mumble")
    backup = os.path.join(rollback_dir, "Mumble-backup")
    make_product(current, "current")
    make_product(backup, "unrelated")
    sentinel_old = current + ".old"
    os.makedirs(sentinel_old)
    sentinel_file = os.path.join(sentinel_old, "unrelated-sentinel.txt")
    open(sentinel_file, "w", encoding="utf-8").close()
    ok, _message = update.rollback(os.path.join(current, "Internal", "app"))
    check("rollback refuses an unowned sibling backup",
          ok is False
          and open(os.path.join(current, "LICENSE"), encoding="utf-8").read()
          == "current")
    check("rollback never deletes a pre-existing Mumble.old sibling",
          os.path.exists(sentinel_file))

    shutil.rmtree(backup)
    owned_backup = os.path.join(rollback_dir, "Mumble-backup-owned")
    make_product(owned_backup, "previous")
    if hasattr(update, "_write_rollback_ownership"):
        update._write_rollback_ownership(current, owned_backup, "a" * 32)
    refreshed = []
    old_refresh = getattr(update, "_refresh_shortcuts", None)
    old_startup = getattr(update, "_startup_enabled", None)
    if old_refresh is not None:
        update._refresh_shortcuts = (
            lambda _app, enabled: refreshed.append(enabled) or True)
    if old_startup is not None:
        update._startup_enabled = lambda: False
    try:
        ok, _message = update.rollback(os.path.join(current, "Internal", "app"))
    finally:
        if old_refresh is not None:
            update._refresh_shortcuts = old_refresh
        if old_startup is not None:
            update._startup_enabled = old_startup
    check("owned path-with-spaces rollback transaction succeeds",
          ok is True
          and open(os.path.join(current, "LICENSE"), encoding="utf-8").read()
          == "previous")
    check("rollback preserves the disabled run-at-login choice",
          refreshed == [False])
    check("owned rollback leaves unrelated sentinel folders untouched",
          os.path.exists(sentinel_file))
finally:
    shutil.rmtree(rollback_dir, ignore_errors=True)

# ---- one real quoted batch transaction in a path containing spaces -------
print("\n== path-with-spaces update transaction smoke ==")
transaction_dir = tempfile.mkdtemp(prefix="Mumble update transaction with spaces ")
try:
    current = os.path.join(transaction_dir, "Mumble Current")
    staged = os.path.join(transaction_dir, "Mumble Staged Product")
    sibling = os.path.join(transaction_dir, "Mumble Sibling")
    make_product(current, "current")
    make_product(staged, "staged")
    make_product(sibling, "sibling")
    launcher = os.path.join(os.environ["SystemRoot"], "System32", "where.exe")
    shutil.copyfile(launcher, os.path.join(current, "Mumble.exe"))
    shutil.copyfile(launcher, os.path.join(staged, "Mumble.exe"))
    old_backup = os.path.join(transaction_dir, "Mumble-backup")
    os.makedirs(old_backup)
    old_sentinel = os.path.join(old_backup, "do-not-touch.txt")
    open(old_sentinel, "w", encoding="utf-8").close()
    original_getpid = update.os.getpid
    update.os.getpid = lambda: 99999999
    try:
        script = update._write_swap_script(
            transaction_dir, staged, current, startup_enabled=False)
    finally:
        update.os.getpid = original_getpid
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", script], capture_output=True, text=True,
        timeout=30)
    check("quoted update transaction completes in a path with spaces",
          result.returncode == 0
          and open(os.path.join(current, "LICENSE"), encoding="utf-8").read()
          == "staged")
    check("transaction leaves sibling install and prior rollback sentinel intact",
          open(os.path.join(sibling, "LICENSE"), encoding="utf-8").read()
          == "sibling" and os.path.exists(old_sentinel))
    check("successful transaction publishes ownership-proven rollback metadata",
          update._load_owned_rollback(current) is not None)
finally:
    shutil.rmtree(transaction_dir, ignore_errors=True)

# ===================================================================== final
if _fails:
    print(f"\n{len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("\nALL GREEN")
sys.exit(0)
