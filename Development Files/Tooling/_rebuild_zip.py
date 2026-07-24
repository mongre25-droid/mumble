#!/usr/bin/env python3
"""Rebuild Mumble.zip — the Windows distributable — from the live tree.

The zip mirrors the SHIPPING layout exactly (the post-restructure app/ layout):

    Mumble/
      Mumble.exe                       <- native launcher (repo root)
      Internal/<launchers .bat/.vbs>   <- Open / Install / Uninstall / Start + the
      Internal/READ ME FIRST.txt          hidden .vbs, READ ME FIRST, README
      Internal/README.md
      Internal/app/<runtime>           <- all runtime code + webui/ + assets/ +
                                          install.ps1/uninstall.ps1 + requirements

Excluded (dev-only, or rebuilt at install):
  .venv/  __pycache__/  *.pyc  test_*.py  Ports/ (macOS/Linux ship via their own
  installers)  llama-cpp-bin/ (owner-gated edge-LLM binaries)  release_assets/
  (legacy pre-restructure launchers).

Builds from SCRATCH by walking the live tree, so a layout error can never
propagate — the previous tool refreshed file *contents* inside whatever (broken)
structure the seed zip already had, and could never move a misplaced launcher.

Run standalone:  python _rebuild_zip.py            (auto-detects the repo root)
            or:  python _rebuild_zip.py <repo-root>
Exit code 0 = built + all layout checks pass; 2 = built but a check FAILED.
"""
import os
import re
import shutil
import sys
import zipfile

APP_REL = os.path.join("Internal", "app")

# Directory names pruned anywhere under Internal/app/.
EXCLUDE_DIRS = {
    ".venv", "__pycache__", "Ports", "llama-cpp-bin", "release_assets",
    "_test_logs", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests",
}


def find_repo_root(start):
    """Walk up from `start` until a dir contains Internal/app/mumble.py."""
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, APP_REL, "mumble.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _skip_file(name):
    return ((name.startswith("test_") and name.endswith(".py"))
            or name in {"requirements-dev.txt", "run_tests.py", "test-results.json"}
            or name.endswith((".pyc", ".pyo")))


def add_tree(z, src_dir, arc_prefix):
    """Add every file under src_dir to the zip at arc_prefix, pruning EXCLUDE_DIRS
    and test/compiled files (the app/ runtime)."""
    n = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in sorted(files):
            if _skip_file(fn):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            z.write(full, arc_prefix + rel)
            n += 1
    return n


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else find_repo_root(__file__)
    if not root or not os.path.isfile(os.path.join(root, APP_REL, "mumble.py")):
        print("repo root not found (no Internal/app/mumble.py). "
              "Pass it explicitly: python _rebuild_zip.py <repo-root>")
        sys.exit(1)

    out = os.path.join(root, "Internal", "Releases", "Mumble.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".new"
    internal = os.path.join(root, "Internal")
    appdir = os.path.join(internal, "app")

    counts = {}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. root Mumble.exe — the native launcher.
        exe = os.path.join(root, "Mumble.exe")
        if os.path.isfile(exe):
            z.write(exe, "Mumble/Mumble.exe")
            counts["Mumble.exe"] = 1
        else:
            print("WARNING: root Mumble.exe missing — zip will lack the launcher.")
        # Ship Mumble's own MIT licence at the distribution root. Third-party
        # component notices live under Internal/app and are included by add_tree.
        licence = os.path.join(root, "LICENSE")
        if os.path.isfile(licence):
            z.write(licence, "Mumble/LICENSE")
            counts["LICENSE"] = 1
        else:
            print("WARNING: root LICENSE missing — zip will lack Mumble's licence.")
        # 2. Internal/ top-level launchers + docs (every file directly under
        #    Internal/, i.e. NOT the app/ dir).
        ni = 0
        for fn in sorted(os.listdir(internal)):
            full = os.path.join(internal, fn)
            if os.path.isfile(full):
                z.write(full, "Mumble/Internal/" + fn)
                ni += 1
        counts["Internal launchers"] = ni
        # 3. Internal/app/ runtime (pruned).
        counts["app files"] = add_tree(z, appdir, "Mumble/Internal/app/")
    os.replace(tmp, out)

    # ---- verify + report -------------------------------------------------
    z2 = zipfile.ZipFile(out)
    names = z2.namelist()
    ver = "?"
    try:
        m = re.search(
            r'VERSION = "([^"]+)"',
            z2.read("Mumble/Internal/app/branding.py").decode("utf-8", "replace"))
        ver = m.group(1) if m else "?"
    except KeyError:
        pass
    print(f"Mumble.zip rebuilt at {out}")
    print(f"  VERSION {ver} · {len(names)} entries")
    for k, v in counts.items():
        print(f"    {k}: {v}")
    checks = {
        "root Mumble.exe": "Mumble/Mumble.exe" in names,
        "root MIT LICENSE": "Mumble/LICENSE" in names,
        "Internal/ launcher (Open Mumble.bat)": "Mumble/Internal/Open Mumble.bat" in names,
        "Internal/ hidden .vbs": "Mumble/Internal/Mumble (hidden).vbs" in names,
        "app runtime (mumble.py)": "Mumble/Internal/app/mumble.py" in names,
        "webui shipped": "Mumble/Internal/app/webui/app.js" in names,
        "Mumble Search runtime shipped": (
            "Mumble/Internal/app/experimental/system_search/engine.py" in names
            and "Mumble/Internal/app/experimental/system_search/ui.js" in names
            and "Mumble/Internal/app/experimental/system_search/ui.css" in names
            and "Mumble/Internal/app/webui/system-search-loader.js" in names),
        "third-party notices shipped": (
            "Mumble/Internal/app/THIRD_PARTY_NOTICES.md" in names),
        "runtime licences shipped": all(
            n in names for n in (
                "Mumble/Internal/app/licenses/computer_control/Apache-2.0.txt",
                "Mumble/Internal/app/licenses/computer_control/comtypes-MIT.txt",
                "Mumble/Internal/app/licenses/computer_control/pywin32-BSD.txt",
            )),
        "installer shipped": "Mumble/Internal/app/install.ps1" in names,
        "NO launchers nested under app/": not any(
            n.startswith("Mumble/Internal/app/") and n.endswith((".bat", ".vbs"))
            for n in names),
        "NO .venv": not any("/.venv/" in n for n in names),
        "NO pytest cache": not any("/.pytest_cache/" in n for n in names),
        "NO tests": not any(re.search(r"/test_[^/]*\.py$", n) for n in names),
        "NO retired computer-control runtime": not any(
            "/experimental/computer_control/" in n
            or n.endswith("/webui/computer-control-loader.js")
            for n in names),
        "NO Ports": not any("/Ports/" in n for n in names),
        "NO llama-cpp-bin": not any("/llama-cpp-bin/" in n for n in names),
    }
    ok = True
    for k, v in checks.items():
        print(f"  [{'ok ' if v else 'BAD'}] {k}")
        ok = ok and v
    if ok:
        # The marketing site serves its own public/ copy. Keep the user-facing
        # download byte-for-byte aligned with the validated root artifact;
        # previously a rebuilt root ZIP left the website serving an older build.
        website_zip = os.path.join(
            root, "Development Files", "Other", "website", "public",
            "Mumble.zip")
        if os.path.isdir(os.path.dirname(website_zip)):
            shutil.copy2(out, website_zip)
            print("  [ok ] website/public/Mumble.zip synced")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
