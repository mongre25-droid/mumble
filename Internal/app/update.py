#!/usr/bin/env python3
"""
Mumble auto-update system.

Checks a hosted JSON manifest for newer versions, downloads and verifies
them, and performs an atomic folder-swap install. Never overwrites the
running installation in-place.

Manifest schema (hosted at a single URL):
{
  "version": "4.6.0",
  "url": "https://.../Mumble-4.6.0.zip",
  "sha256": "abc123...",
  "signature": "<ed25519 hex over version\\nurl\\nsha256>",
  "min_supported": "4.0.0",
  "notes": "What's new in this release.",
  "mandatory": false
}

Flow:
1. Fetch manifest → compare semver to branding.VERSION
2. If newer: notify via tray/island
3. Download zip, verify sha256, extract to versioned folder
4. Write update.bat that swaps folders + relaunches
5. Keep previous version for rollback
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# ---- Update manifest URL (owner sets this) ----
# Host this on GitHub Pages: https://<owner>.github.io/mumble-updates/update.json
# See HANDBOOK.md "Hosting Updates" for setup instructions.
MANIFEST_URL = "https://mumble-app.github.io/mumble-updates/update.json"

# ---- Update authenticity (T7) ----
# Updates are INTEGRITY-checked (mandatory sha256, below). They are not yet
# AUTHENTICITY-checked unless the owner activates signing. sha256 only proves the
# zip matches the manifest — it does NOT prove the manifest itself is genuine, so
# anyone able to serve/redirect MANIFEST_URL could ship a malicious zip with a
# matching hash. To activate enforced signing (fail-closed):
#   1. add `cryptography` to requirements.txt,
#   2. generate an ed25519 keypair (keep the private key OFFLINE, owner-only),
#   3. paste the PUBLIC key (hex, 64 chars) into UPDATE_PUBLIC_KEY below,
#   4. sign each manifest's canonical payload (_manifest_signed_payload) and put
#      the hex signature in the manifest "signature" field.
# Until a publisher key is provisioned the production update channel is disabled.
# There is never a sha256-only downgrade: a hash does not authenticate a publisher.
UPDATE_PUBLIC_KEY = ""  # owner: Ed25519 public key, hex.
_INSTALL_LOCK = threading.Lock()


def _is_https_url(value):
    """Strict allow-list for updater network entry points."""
    try:
        url = str(value or "")
        if url != url.strip() or "\\" in url or any(ord(ch) < 33 for ch in url):
            return False
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme.lower() == "https" and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


def update_channel_enabled():
    """Return whether production update checks are safe to expose."""
    return bool(_is_https_url(MANIFEST_URL)
                and (UPDATE_PUBLIC_KEY or "").strip())


def _manifest_signed_payload(manifest):
    """The canonical bytes a manifest signature covers — the security-critical
    fields in a fixed order so signer and verifier agree byte-for-byte."""
    return (
        f"{manifest.get('version', '')}\n"
        f"{manifest.get('url', '')}\n"
        f"{manifest.get('sha256', '')}"
    ).encode("utf-8")


def _verify_manifest_signature(manifest):
    """Authenticity gate (T7). Returns (ok, status):
      • no public key configured         -> (False, reason); channel disabled.
      • key configured + valid signature -> (True, 'signed').
      • key configured but signature missing / crypto lib absent / signature
        invalid                          -> (False, reason)  — FAIL CLOSED.
    A configured key means signatures are REQUIRED; we never downgrade to
    unsigned once the owner has opted in."""
    pub_hex = (UPDATE_PUBLIC_KEY or "").strip()
    if not pub_hex:
        return False, "publisher signature verification is not configured"
    sig_hex = str(manifest.get("signature", "") or "").strip()
    if not sig_hex:
        return False, "manifest is unsigned but signing is required"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except Exception:
        return False, "signing required but the crypto library is missing"
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(bytes.fromhex(sig_hex), _manifest_signed_payload(manifest))
        return True, "signed"
    except InvalidSignature:
        return False, "signature is invalid"
    except Exception as e:
        return False, f"signature check failed: {e}"


def _parse_semver(v):
    """Parse a semver string like '4.6.0' into a comparable tuple.
    Short versions like '4.0' are padded to 3 components: (4, 0, 0)."""
    try:
        parts = str(v).strip().split(".")
        tup = tuple(int(x) for x in parts[:3])
        # Pad to 3 components so '4.0' compares equal to '4.0.0'
        return tup + (0,) * (3 - len(tup)) if len(tup) < 3 else tup
    except Exception:
        return (0, 0, 0)


def _verify_sha256(filepath, expected):
    """Verify a file's SHA256 hash. Returns True on match.
    A missing or empty expected hash ALWAYS fails — never trust an unverifiable download."""
    if not expected or not str(expected).strip():
        return False
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def check_for_update(manifest_url=None):
    """Fetch the manifest and compare to current version.
    Returns (update_available, manifest_dict):
      (True,  manifest) — an applicable update exists
      (False, {})       — check SUCCEEDED, already up to date / not applicable
      (False, None)     — check FAILED (no URL, network error, bad manifest)
    Callers must distinguish {} from None — conflating them used to surface
    'Could not reach update server' on every successful up-to-date check."""
    url = manifest_url or MANIFEST_URL
    if manifest_url is None and not update_channel_enabled():
        return False, {}
    if not url:
        return False, None
    if not _is_https_url(url):
        print("update check refused: manifest URL must be https")
        return False, None

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mumble-Update/1.0"})
        # URL and redirect target are both checked by _is_https_url.
        with urllib.request.urlopen(req, timeout=15) as r:  # nosec B310
            final_url = getattr(r, "geturl", lambda: url)()
            if not _is_https_url(final_url):
                raise RuntimeError("manifest redirected to a non-HTTPS URL")
            manifest = json.load(r)
    except Exception as e:
        print(f"update check failed: {e}")
        return False, None

    if not isinstance(manifest, dict) or "version" not in manifest:
        return False, None

    # Authenticity gate (T7): verify the manifest signature BEFORE trusting any
    # of its fields (version/url/sha256). Fail-closed once the owner configures a
    # public key. Production checks are disabled until that key is configured.
    sig_ok, sig_status = _verify_manifest_signature(manifest)
    if not sig_ok:
        print(f"update check refused: {sig_status}")
        return False, None

    # Import branding lazily (may not be on path during install)
    try:
        import branding

        current = _parse_semver(branding.VERSION)
    except Exception:
        return False, None

    latest = _parse_semver(manifest["version"])
    if latest <= current:
        return False, {}  # check succeeded — already up to date

    # Check minimum supported version
    min_v = manifest.get("min_supported", "0.0.0")
    if current < _parse_semver(min_v):
        print(f"update requires at least v{min_v}; current is v{branding.VERSION}")
        return False, {}  # check succeeded — update exists but isn't applicable

    return True, manifest


def download_and_install(manifest, install_dir, callback=None):
    """Download and install an update in a background thread.
    callback(status, message) is called with progress updates."""

    def _run():
        tmp = None
        extracted_dir = None
        staged_product = None
        keep_staged_product = False
        if not _INSTALL_LOCK.acquire(blocking=False):
            if callback:
                callback("error", "Another update is already being installed.")
            return
        try:
            # Do not rely on every caller having gone through
            # check_for_update(); this function is also an API boundary.
            sig_ok, sig_status = _verify_manifest_signature(manifest)
            if not sig_ok:
                if callback:
                    callback("error", f"Update refused â€” {sig_status}.")
                return
            if callback:
                callback("downloading", "Downloading update...")

            # Download to temp file (unique per invocation to avoid race).
            # Chunked urlopen WITH a timeout — urlretrieve has none, so a
            # stalled connection used to hang this thread forever after
            # "Downloading update…" with no error ever surfacing.
            url = manifest["url"]
            if not _is_https_url(url):
                if callback:
                    callback("error", "Update refused — download URL must be https.")
                return
            fd, tmp = tempfile.mkstemp(prefix="mumble_update_", suffix=".zip")
            os.close(fd)
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mumble-Update/1.0"}
            )
            # URL and redirect target are both checked by _is_https_url.
            with urllib.request.urlopen(req, timeout=60) as r, open(  # nosec B310
                    tmp, "wb") as out:
                final_url = getattr(r, "geturl", lambda: url)()
                if not _is_https_url(final_url):
                    raise RuntimeError("download redirected to a non-HTTPS URL")
                shutil.copyfileobj(r, out, length=65536)

            # Verify hash — sha256 is MANDATORY; never trust an unverifiable download
            expected_hash = manifest.get("sha256", "")
            if not expected_hash or not str(expected_hash).strip():
                if callback:
                    callback("error", "Update manifest missing SHA256 — update aborted.")
                return
            if not _verify_sha256(tmp, expected_hash):
                if callback:
                    callback("error", "SHA256 verification failed — update aborted.")
                return

            if callback:
                callback("extracting", "Installing update...")

            # Extract to versioned folder (with zip-slip protection)
            version = manifest["version"]
            # Sanitize version: only allow alphanumerics, dots, and hyphens
            # to prevent path traversal via crafted version strings (e.g. "..\\..\\evil")
            import re
            if not re.match(r'^[A-Za-z0-9.\-]+$', str(version)):
                if callback:
                    callback("error", "Update refused — invalid version in manifest.")
                return
            product_dir = _installed_product_root(install_dir)
            if not product_dir:
                if callback:
                    callback(
                        "error",
                        "Update refused — the installed product layout is incomplete.")
                return
            parent = os.path.dirname(product_dir)
            # Unique siblings cannot collide with another extracted/installed
            # copy whose folder happens to contain the release version.
            extracted_dir = tempfile.mkdtemp(
                prefix=f"Mumble-{version}-extract-", dir=parent)

            real_new = os.path.realpath(extracted_dir)
            with zipfile.ZipFile(tmp, "r") as zf:
                for member in zf.namelist():
                    # Abort the ENTIRE extraction on any unsafe member — silently
                    # skipping just the bad entry still installs the attacker's
                    # "safe" subset of a tampered archive.
                    norm = member.replace("\\", "/")
                    if os.path.isabs(member) or norm.startswith("/") or ".." in norm.split("/"):
                        raise RuntimeError(f"unsafe path in update archive: {member}")
                    member_path = os.path.realpath(os.path.join(extracted_dir, member))
                    if not (member_path == real_new
                            or member_path.startswith(real_new + os.sep)):
                        raise RuntimeError(f"zip-slip blocked: {member}")
                    zf.extract(member, extracted_dir)

            # The release zip is the complete product tree. Swap that complete
            # root so package-level files cannot be silently discarded.
            product_root = _find_product_root(extracted_dir)
            if not product_root:
                if callback:
                    callback(
                        "error",
                        "Update archive layout unexpected (no complete product found) — aborted.")
                return
            staged_product = tempfile.mkdtemp(
                prefix=f"Mumble-{version}-product-", dir=parent)
            os.rmdir(staged_product)
            shutil.move(product_root, staged_product)
            shutil.rmtree(extracted_dir, ignore_errors=True)

            # Swap the full product, carry the app environment, and preserve a
            # complete rollback copy.
            _write_swap_script(parent, staged_product, product_dir)
            keep_staged_product = True

            if callback:
                callback("ready", f"Update v{version} ready — restart to apply.")

        except Exception as e:
            print(f"update install failed: {e}")
            if callback:
                callback("error", f"Update failed: {e}")
        finally:
            # Always remove the downloaded temp zip — the inline success-path
            # cleanup left a multi-MB file in %TEMP% on EVERY failure path (SHA
            # mismatch, extract error, exception), and the PID+timestamp name
            # meant repeated failures accumulated distinct orphans.
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if not keep_staged_product:
                for path in (extracted_dir, staged_product):
                    if path:
                        shutil.rmtree(path, ignore_errors=True)
            _INSTALL_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()


def _find_product_root(base):
    """Locate exactly one canonical complete product root in an extraction."""
    matches = []
    for root, _dirs, files in os.walk(base):
        app_dir = os.path.join(root, "Internal", "app")
        if (
            "Mumble.exe" in files
            and "LICENSE" in files
            and os.path.isfile(os.path.join(app_dir, "mumble.py"))
            and os.path.isfile(os.path.join(app_dir, "branding.py"))
            and os.path.isfile(os.path.join(app_dir, "install.ps1"))
            and os.path.isfile(os.path.join(app_dir, "uninstall.ps1"))
        ):
            matches.append(root)
    if len(matches) == 1:
        return matches[0]
    return None


def _installed_product_root(install_dir):
    """Resolve Internal/app to its complete product root, failing closed."""
    app_dir = os.path.abspath(install_dir)
    internal_dir = os.path.dirname(app_dir)
    product_dir = os.path.dirname(internal_dir)
    if (
        os.path.basename(app_dir).casefold() != "app"
        or os.path.basename(internal_dir).casefold() != "internal"
        or not os.path.isfile(os.path.join(app_dir, "mumble.py"))
        or not os.path.isfile(os.path.join(product_dir, "Mumble.exe"))
    ):
        return None
    return product_dir


def pending_update_script(install_dir):
    """Return the swap script path for a canonical product layout."""
    product_dir = _installed_product_root(install_dir)
    if not product_dir:
        return ""
    return os.path.join(os.path.dirname(product_dir), "apply_update.bat")


def _write_swap_script(parent_dir, new_dir, current_dir):
    """Write a batch script that swaps complete old and new product roots."""
    script = os.path.join(parent_dir, "apply_update.bat")
    pid = os.getpid()
    with open(script, "w") as f:
        f.write("@echo off\n")
        f.write("echo Applying Mumble update...\n")
        f.write("timeout /t 2 /nobreak >nul\n")
        # Kill only THIS Mumble instance by PID (not all Python processes).
        # (No wmic fallback: WMIC is removed on Windows 11 24H2+, and the WQL
        # string embedded unescaped backslashes — the PID kill is sufficient.)
        f.write(f"taskkill /f /pid {pid} 2>nul\n")
        f.write("timeout /t 1 /nobreak >nul\n")
        # Rename current to backup
        backup = os.path.join(parent_dir, "Mumble-backup")
        f.write(f'if exist "{backup}" rmdir /s /q "{backup}"\n')
        f.write(f'rename "{current_dir}" "Mumble-backup"\n')
        f.write("if errorlevel 1 goto launch\n")
        # Rename new to current
        f.write(f'rename "{new_dir}" "{os.path.basename(current_dir)}"\n')
        f.write("if errorlevel 1 goto failed_restore\n")
        f.write("goto launch\n")
        f.write(":failed_restore\n")
        f.write(f'rename "{backup}" "{os.path.basename(current_dir)}"\n')
        f.write(":launch\n")
        # The release excludes .venv. Carry the app environment from the complete
        # backup, then refresh dependencies, branding, and canonical shortcuts.
        app_rel = os.path.join("Internal", "app")
        current_app = os.path.join(current_dir, app_rel)
        backup_app = os.path.join(backup, app_rel)
        f.write(
            f'if not exist "{current_app}\\.venv" if exist "{backup_app}\\.venv" '
            f'xcopy /e /i /q /y "{backup_app}\\.venv" "{current_app}\\.venv" >nul\n'
        )
        f.write(
            f'if exist "{current_app}\\.venv\\Scripts\\python.exe" '
            f'"{current_app}\\.venv\\Scripts\\python.exe" -m pip install -r '
            f'"{current_app}\\requirements.txt" --quiet --disable-pip-version-check\n'
        )
        f.write(
            f'if exist "{current_app}\\.venv\\Scripts\\python.exe" '
            f'"{current_app}\\.venv\\Scripts\\python.exe" '
            f'"{current_app}\\brand_exe.py" >nul 2>nul\n'
        )
        f.write(f'pushd "{current_app}"\n')
        f.write(
            'if exist ".venv\\Scripts\\python.exe" '
            '".venv\\Scripts\\python.exe" -c '
            '"import autostart; autostart.install_start_menu(); '
            'autostart.install_desktop_shortcut(); '
            'autostart.install_uninstall_start_menu(); autostart.enable()" '
            '>nul 2>nul\n'
        )
        f.write("popd\n")
        # NOTE: Mumble-backup is intentionally KEPT for rollback() (the previous
        # version restore). It holds its own .venv copy — disk cost is the price of
        # a safe rollback.
        # Relaunch — prefer the branded Mumble.exe (Task Manager + branding), and
        # fall back to pythonw.exe only if it isn't present.
        root_launcher = os.path.join(current_dir, "Mumble.exe")
        branded = os.path.join(current_app, ".venv", "Scripts", "Mumble.exe")
        f.write(f'if exist "{root_launcher}" goto relaunch_root\n')
        f.write(f'if exist "{branded}" goto relaunch_exe\n')
        f.write(
            f'start "" "{current_app}\\.venv\\Scripts\\pythonw.exe" "{current_app}\\mumble.py"\n'
        )
        f.write("goto done\n")
        f.write(":relaunch_root\n")
        f.write(f'start "" "{root_launcher}"\n')
        f.write("goto done\n")
        f.write(":relaunch_exe\n")
        f.write(f'start "" "{branded}" "{current_app}\\mumble.py"\n')
        f.write(":done\n")
        f.write("echo Update complete!\n")


def rollback(install_dir):
    """Restore the previous version from backup."""
    product_dir = _installed_product_root(install_dir)
    if not product_dir:
        return False, "Installed product layout is incomplete."
    parent = os.path.dirname(product_dir)
    backup = os.path.join(parent, "Mumble-backup")
    if not os.path.exists(backup):
        return False, "No backup found."
    try:
        current_backup = product_dir + ".old"
        if os.path.exists(current_backup):
            shutil.rmtree(current_backup)
        shutil.move(product_dir, current_backup)
        try:
            shutil.move(backup, product_dir)
        except Exception:
            # Second move failed — restore what we just set aside so we never
            # strand the user with NO install at all.
            shutil.move(current_backup, product_dir)
            raise
        return True, "Rolled back to previous version."
    except Exception as e:
        return False, str(e)


# ---- Background auto-check on boot ----


def start_auto_check(on_available=None, on_error=None):
    """Launch a background thread that checks for updates on boot.
    on_available(manifest) is called (on the caller's thread) if an update is found.
    on_error(msg) is called if the check itself fails. Either may be None."""

    def _bg():
        ok, manifest = check_for_update()
        if ok and manifest:
            if on_available:
                on_available(manifest)
        elif manifest is None and on_error:
            on_error("Could not reach update server.")

    threading.Thread(target=_bg, daemon=True).start()
