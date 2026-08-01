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
import importlib.util
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_REL = os.path.join("Internal", "app")

# Directory names pruned anywhere under Internal/app/.
EXCLUDE_DIRS = {
    ".venv", "__pycache__", "Ports", "llama-cpp-bin", "release_assets",
    "_test_logs", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests",
}

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

TEXT_EXTENSIONS = {
    ".bat", ".cfg", ".cmd", ".css", ".csv", ".desktop", ".html",
    ".ini", ".js", ".json", ".jsx", ".md", ".ps1", ".py", ".service",
    ".sh", ".sql", ".svg", ".toml", ".ts", ".tsx", ".txt", ".vbs", ".xml",
    ".yaml", ".yml",
}
TEXT_NAMES = {"LICENSE", "NOTICE"}


def _load_provenance(root):
    path = Path(root) / "Development Files" / "Tooling" / "release_provenance.py"
    spec = importlib.util.spec_from_file_location("mumble_release_provenance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"release provenance authority is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packaged_bytes(source):
    """Return canonical package bytes while leaving binary files untouched."""
    with open(source, "rb") as handle:
        data = handle.read()
    name = os.path.basename(os.fspath(source))
    extension = os.path.splitext(name)[1].lower()
    if extension in TEXT_EXTENSIONS or name.upper() in TEXT_NAMES:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def write_file(archive, source, archive_name):
    """Write one deterministic member independent of timestamps/line endings."""
    info = zipfile.ZipInfo(archive_name.replace("\\", "/"), ZIP_EPOCH)
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, packaged_bytes(source), compresslevel=9)


def write_bytes(archive, data, archive_name):
    """Write one deterministic generated member."""
    info = zipfile.ZipInfo(archive_name.replace("\\", "/"), ZIP_EPOCH)
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=9)


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


def release_sources(root):
    """Return the canonical ordered (source, archive-name) release manifest."""
    root = Path(root)
    tracked = set(subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True,
        capture_output=True,
    ).stdout.decode("utf-8").rstrip("\0").split("\0"))

    def require_tracked(source):
        relative = source.relative_to(root).as_posix()
        if relative not in tracked:
            raise RuntimeError(f"unexpected untracked release source: {relative}")
        return source

    internal = root / "Internal"
    appdir = internal / "app"
    sources = []

    for source, archive_name in (
        (root / "Mumble.exe", "Mumble/Mumble.exe"),
        (root / "LICENSE", "Mumble/LICENSE"),
        (root / "Development Files" / "Legal" / "release-inventory.json",
         "Mumble/RELEASE-INVENTORY.json"),
        (root / "Development Files" / "Legal" / "dependency-lock.json",
         "Mumble/DEPENDENCY-CLOSURE.json"),
    ):
        if source.is_file():
            sources.append((require_tracked(source), archive_name))

    for source in sorted(path for path in internal.iterdir() if path.is_file()):
        sources.append((require_tracked(source), f"Mumble/Internal/{source.name}"))

    for current, dirs, files in os.walk(appdir):
        dirs[:] = sorted(directory for directory in dirs if directory not in EXCLUDE_DIRS)
        for filename in sorted(files):
            if _skip_file(filename):
                continue
            source = Path(current) / filename
            relative = source.relative_to(appdir).as_posix()
            sources.append((require_tracked(source), f"Mumble/Internal/app/{relative}"))
    return sources


def website_archive_path(root):
    """Return the maintained website's release-download destination."""
    return (
        Path(root) / "Development Files" / "Marketing" / "Website" /
        "public" / "Mumble.zip"
    )


def sync_website_archive(root, canonical_archive):
    """Synchronize the validated archive without inventing missing structure."""
    destination = website_archive_path(root)
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            "Expected maintained website release directory is missing: "
            f"{destination.parent}"
        )
    shutil.copy2(canonical_archive, destination)
    if destination.read_bytes() != Path(canonical_archive).read_bytes():
        raise RuntimeError(
            "Maintained website release archive did not match the canonical "
            f"artifact after synchronization: {destination}"
        )
    return destination


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else find_repo_root(__file__)
    if not root or not os.path.isfile(os.path.join(root, APP_REL, "mumble.py")):
        print("repo root not found (no Internal/app/mumble.py). "
              "Pass it explicitly: python _rebuild_zip.py <repo-root>")
        sys.exit(1)

    out = os.path.join(root, "Internal", "Releases", "Mumble.zip")
    website_zip = website_archive_path(root)
    if not website_zip.parent.is_dir():
        print(
            "ERROR: expected maintained website release directory is missing: "
            f"{website_zip.parent}"
        )
        sys.exit(1)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".new"
    sources = release_sources(root)
    provenance = _load_provenance(root)
    payload_members = {
        archive_name.removeprefix("Mumble/"): (packaged_bytes(source), 0o644)
        for source, archive_name in sources
    }
    provenance_bytes = provenance.generate_release_provenance(
        repo_root=root,
        platform="windows",
        architecture="x86_64",
        package_format="zip",
        members=payload_members,
        entrypoint="Mumble.exe",
        installer="Internal/app/install.ps1",
        uninstaller="Internal/app/uninstall.ps1",
        assets=("Internal/app/assets/mumble.ico", "Internal/app/webui/mumble.png"),
        migrations_config=(
            "Internal/app/settings.py",
            "Internal/app/cloud_schema.sql",
            "Internal/app/update.py",
        ),
        evidence={
            "update": {
                "status": "passed",
                "reference": "Internal/app/test_update.py",
                "reason": "Focused source automation; not an installed-machine update.",
            },
            "rollback": {
                "status": "passed",
                "reference": "Internal/app/test_update.py",
                "reason": "Focused source automation; not an installed-machine rollback.",
            },
            "smoke": {
                "status": "passed",
                "reference": "https://github.com/mongre25-droid/mumble/actions/runs/30721075874",
                "reason": "Exact-base automated package and test evidence only.",
            },
            "physical": {
                "status": "unavailable",
                "reference": None,
                "reason": "No disposable installed-machine or physical workflow pass is available.",
            },
        },
    )

    counts = {}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        source_names = {archive_name for _, archive_name in sources}
        if "Mumble/Mumble.exe" not in source_names:
            print("WARNING: root Mumble.exe missing — zip will lack the launcher.")
        if "Mumble/LICENSE" not in source_names:
            print("WARNING: root LICENSE missing — zip will lack Mumble's licence.")
        for source, archive_name in sources:
            write_file(z, source, archive_name)
        write_bytes(z, provenance_bytes, "Mumble/RELEASE-PROVENANCE.json")
        counts["Mumble.exe"] = int("Mumble/Mumble.exe" in source_names)
        counts["LICENSE"] = int("Mumble/LICENSE" in source_names)
        counts["Internal launchers"] = sum(
            name.startswith("Mumble/Internal/")
            and not name.startswith("Mumble/Internal/app/")
            for name in source_names
        )
        counts["app files"] = sum(
            name.startswith("Mumble/Internal/app/") for name in source_names
        )
    os.replace(tmp, out)

    # ---- verify + report -------------------------------------------------
    z2 = zipfile.ZipFile(out)
    names = z2.namelist()
    archive_members = {
        info.filename.removeprefix("Mumble/"): (
            z2.read(info), (info.external_attr >> 16) & 0o7777)
        for info in z2.infolist()
        if info.filename.startswith("Mumble/")
    }
    provenance.validate_release_provenance(
        archive_members[provenance.PROVENANCE_NAME][0],
        repo_root=root,
        members=archive_members,
    )
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
        "complete release provenance": "Mumble/RELEASE-PROVENANCE.json" in names,
        "machine-readable licence tracker": "Mumble/RELEASE-INVENTORY.json" in names,
        "Internal/ launcher (Open Mumble.bat)": "Mumble/Internal/Open Mumble.bat" in names,
        "Internal/ hidden .vbs": "Mumble/Internal/Mumble (hidden).vbs" in names,
        "app runtime (mumble.py)": "Mumble/Internal/app/mumble.py" in names,
        "processing route policy shipped": "Mumble/Internal/app/processing_route.py" in names,
        "webui shipped": "Mumble/Internal/app/webui/app.js" in names,
        "Focus Stage foundation shipped": all(
            n in names for n in (
                "Mumble/Internal/app/webui/focus-stage-contract.json",
                "Mumble/Internal/app/webui/focus-stage.js",
                "Mumble/Internal/app/webui/focus-stage.css",
            )),
        "Mumble Find runtime and native drag shipped": (
            "Mumble/Internal/app/mumble_find.py" in names
            and "Mumble/Internal/app/experimental/system_search/engine.py" in names
            and "Mumble/Internal/app/experimental/system_search/native_drag.py" in names
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
        sync_website_archive(root, out)
        print(f"  [ok ] maintained website archive synced: {website_zip}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
