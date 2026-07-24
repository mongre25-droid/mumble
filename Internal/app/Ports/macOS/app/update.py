
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
import sys
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
                    callback("error", f"Update refused — {sig_status}.")
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
            parent = os.path.dirname(install_dir)
            new_dir = os.path.join(parent, f"Mumble-{version}")
            if os.path.exists(new_dir):
                shutil.rmtree(new_dir)
            os.makedirs(new_dir, exist_ok=True)

            real_new = os.path.realpath(new_dir)
            with zipfile.ZipFile(tmp, "r") as zf:
                for member in zf.namelist():
                    # Abort the ENTIRE extraction on any unsafe member — silently
                    # skipping just the bad entry still installs the attacker's
                    # "safe" subset of a tampered archive.
                    norm = member.replace("\\", "/")
                    if os.path.isabs(member) or norm.startswith("/") or ".." in norm.split("/"):
                        raise RuntimeError(f"unsafe path in update archive: {member}")
                    member_path = os.path.realpath(os.path.join(new_dir, member))
                    if not (member_path == real_new
                            or member_path.startswith(real_new + os.sep)):
                        raise RuntimeError(f"zip-slip blocked: {member}")
                    zf.extract(member, new_dir)

            # The release zip is the FULL product tree (Mumble/Internal/app/...),
            # but the swap replaces install_dir (the app/ runtime dir). Locate the
            # app/ subtree — the dir holding the platform entry point — inside
            # the extraction and
            # flatten it next to install_dir so the same-dir rename swap works.
            app_root = _find_app_root(new_dir)
            if not app_root:
                if callback:
                    callback(
                        "error",
                        "Update archive layout unexpected (no runtime found) — aborted.")
                return
            flat_new = os.path.join(parent, f"Mumble-{version}-app")
            if os.path.exists(flat_new):
                shutil.rmtree(flat_new)
            shutil.move(app_root, flat_new)
            shutil.rmtree(new_dir, ignore_errors=True)  # drop the extraction wrapper

            # Write update swap script (swaps install_dir <-> flat_new, carries the
            # .venv over + refreshes deps — the zip ships no .venv).
            _write_swap_script(parent, flat_new, install_dir)

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
            _INSTALL_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()


def _find_app_root(base):
    """Locate the runtime dir (entry point + branding.py) inside an
    extracted release tree. The release zip is the full product
    (Mumble/Internal/app/...); the updater swaps the app/ subtree, so find it.
    Returns the path, or None. Also handles a bare-app zip (base itself is it)."""
    for root, _dirs, files in os.walk(base):
        if ("branding.py" in files
                and ("mumble_mac.py" in files or "mumble.py" in files)):
            return root
    return None


def _write_swap_script(parent_dir, new_dir, current_dir):
    """Write the platform-appropriate update swap script.

    macOS: .command shell script (kill / swap / relaunch via open).
    Windows: .bat batch script (taskkill / rename / start).
    Linux: bash script (kill / mv / relaunch).
    """
    pid = os.getpid()

    if sys.platform == "darwin":
        # macOS: .command shell script — double-clickable in Finder, invokable
        # from Terminal. Uses kill/mv/open for a safe atomic swap.
        script = os.path.join(parent_dir, "apply_update.command")
        with open(script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("echo 'Applying Mumble update...'\n")
            f.write("sleep 2\n")
            # Kill only THIS Mumble process by PID
            f.write(f"kill {pid} 2>/dev/null\n")
            f.write("sleep 1\n")
            backup = os.path.join(parent_dir, "Mumble-backup")
            # Remove old backup if present
            f.write(f'if [ -d "{backup}" ]; then rm -rf "{backup}"; fi\n')
            # Move current to backup, then new into place
            f.write(f'mv "{current_dir}" "{backup}" || exit 0\n')
            f.write(f'mv "{new_dir}" "{current_dir}" || (\n')
            f.write(f'  mv "{backup}" "{current_dir}"\n')
            f.write('  echo "Update failed — restored previous version."\n')
            f.write('  exit 1\n')
            f.write(')\n')
            # Release archives exclude virtual environments. Carry the known-good
            # interpreter from the rollback copy before refreshing requirements.
            f.write(f'if [ ! -d "{current_dir}/.venv" ] && [ -d "{backup}/.venv" ]; then\n')
            f.write(f'  cp -R "{backup}/.venv" "{current_dir}/.venv"\n')
            f.write('fi\n')
            # Re-run pip install so any new dependencies are available after
            # the swap (PI-004 / VAL-MAC-015). The venv lives inside the app
            # directory; the zip may ship an updated requirements.txt.
            f.write('echo "Updating Python dependencies..."\n')
            f.write(f'if [ -d "{current_dir}/.venv" ]; then\n')
            f.write(f'  "{current_dir}/.venv/bin/python" -m pip install '
                    f'-r "{current_dir}/requirements.txt" -q 2>/dev/null || true\n')
            f.write('fi\n')
            # Strip quarantine + ad-hoc code-sign so Gatekeeper won't block the
            # freshly-extracted bundle (PI-003 / VAL-MAC-014). Ad-hoc signing
            # ("-" = sign locally, free) gives the app a stable identity so
            # macOS keeps Microphone/Accessibility permissions after the update.
            f.write(f'xattr -dr com.apple.quarantine "{current_dir}" 2>/dev/null || true\n')
            f.write('xattr -dr com.apple.quarantine "$HOME/Applications/Mumble.app" 2>/dev/null || true\n')
            f.write('codesign --force --deep --sign - "$HOME/Applications/Mumble.app" 2>/dev/null || true\n')
            # Relaunch via the .command launcher or directly
            launch_cmd = os.path.join(parent_dir, "Launch Mumble.command")
            if os.path.exists(launch_cmd):
                f.write(f'open "{launch_cmd}"\n')
            else:
                f.write(f'if [ -x "{current_dir}/.venv/bin/python" ]; then\n')
                f.write(f'  "{current_dir}/.venv/bin/python" "{current_dir}/mumble_mac.py" &\n')
                f.write('else\n')
                f.write(f'  /usr/bin/python3 "{current_dir}/mumble_mac.py" &\n')
                f.write('fi\n')
            f.write("echo 'Update complete!'\n")
        # Make it executable
        try:
            os.chmod(script, 0o755)
        except OSError:
            pass

    elif sys.platform.startswith("win"):
        # Windows: .bat batch script
        script = os.path.join(parent_dir, "apply_update.bat")
        with open(script, "w") as f:
            f.write("@echo off\n")
            f.write("echo Applying Mumble update...\n")
            f.write("timeout /t 2 /nobreak >nul\n")
            f.write(f"taskkill /f /pid {pid} 2>nul\n")
            f.write("timeout /t 1 /nobreak >nul\n")
            backup = os.path.join(parent_dir, "Mumble-backup")
            f.write(f'if exist "{backup}" rmdir /s /q "{backup}"\n')
            f.write(f'rename "{current_dir}" "Mumble-backup"\n')
            f.write("if errorlevel 1 goto launch\n")
            f.write(f'rename "{new_dir}" "{os.path.basename(current_dir)}"\n')
            f.write("if errorlevel 1 goto failed_restore\n")
            f.write("goto launch\n")
            f.write(":failed_restore\n")
            f.write(f'rename "{backup}" "{os.path.basename(current_dir)}"\n')
            f.write(":launch\n")
            f.write(f'if exist "{current_dir}\\Mumble.exe" goto relaunch_exe\n')
            f.write(
                f'start "" "{current_dir}\\.venv\\Scripts\\pythonw.exe" '
                f'"{current_dir}\\mumble.py"\n'
            )
            f.write("goto done\n")
            f.write(":relaunch_exe\n")
            f.write(f'start "" "{current_dir}\\Mumble.exe"\n')
            f.write(":done\n")
            f.write("echo Update complete!\n")

    else:
        # Linux: bash script
        script = os.path.join(parent_dir, "apply_update.sh")
        with open(script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("echo 'Applying Mumble update...'\n")
            f.write("sleep 2\n")
            f.write(f"kill {pid} 2>/dev/null\n")
            f.write("sleep 1\n")
            backup = os.path.join(parent_dir, "Mumble-backup")
            f.write(f'if [ -d "{backup}" ]; then rm -rf "{backup}"; fi\n')
            f.write(f'mv "{current_dir}" "{backup}" || exit 0\n')
            f.write(f'mv "{new_dir}" "{current_dir}" || (\n')
            f.write(f'  mv "{backup}" "{current_dir}"\n')
            f.write('  echo "Update failed — restored previous version."\n')
            f.write('  exit 1\n')
            f.write(')\n')
            f.write(f'if [ ! -d "{current_dir}/.venv" ] && [ -d "{backup}/.venv" ]; then\n')
            f.write(f'  cp -R "{backup}/.venv" "{current_dir}/.venv"\n')
            f.write('fi\n')
            f.write(f'if [ -x "{current_dir}/.venv/bin/python" ]; then\n')
            f.write(f'  "{current_dir}/.venv/bin/python" -m pip install -r "{current_dir}/requirements.txt" -q 2>/dev/null || true\n')
            f.write(f'  "{current_dir}/.venv/bin/python" "{current_dir}/mumble_linux.py" &\n')
            f.write('else\n')
            f.write(f'  /usr/bin/python3 "{current_dir}/mumble_linux.py" &\n')
            f.write('fi\n')
            f.write("echo 'Update complete!'\n")
        try:
            os.chmod(script, 0o755)
        except OSError:
            pass


def rollback(install_dir):
    """Restore the previous version from backup."""
    parent = os.path.dirname(install_dir)
    backup = os.path.join(parent, "Mumble-backup")
    if not os.path.exists(backup):
        return False, "No backup found."
    try:
        current_backup = install_dir + ".old"
        if os.path.exists(current_backup):
            shutil.rmtree(current_backup)
        shutil.move(install_dir, current_backup)
        try:
            shutil.move(backup, install_dir)
        except Exception:
            # Second move failed — restore what we just set aside so we never
            # strand the user with NO install at all.
            shutil.move(current_backup, install_dir)
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
