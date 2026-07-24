#!/usr/bin/env python3
"""Build a clean MacMumble.zip from the current source tree.

Cross-platform replacement for build_mac_release.sh (which needs a Mac `zip`).
This works on Windows too and - crucially - preserves the Unix execute bit on the
`.command` launchers, so they stay double-clickable on the Mac after unzip.

Excludes the dev venv, caches, and junk. The zip mirrors build_mac_release.sh:
every entry is under a top-level "MacMumble/" folder. Output: <repo>/MacMumble.zip.
"""
import os
import stat
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))          # .../MacMumble
ROOT = os.path.dirname(SRC)                               # repo root
ZIP = os.path.join(ROOT, "MacMumble.zip")
TOP = "MacMumble"

EXCLUDE_DIRS = {".venv", "__pycache__", ".git"}
EXCLUDE_NAMES = {".DS_Store"}
EXEC_EXT = (".command", ".sh")                            # need the +x bit


def _mode_bits(path):
    """0755 for launchers/scripts, 0644 otherwise - encoded for the zip."""
    is_exec = path.lower().endswith(EXEC_EXT)
    perm = 0o755 if is_exec else 0o644
    return (stat.S_IFREG | perm) << 16


def main():
    count, execs = 0, []
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in filenames:
                if name in EXCLUDE_NAMES:
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, SRC).replace(os.sep, "/")
                arc = f"{TOP}/{rel}"
                with open(full, "rb") as f:
                    data = f.read()
                info = zipfile.ZipInfo(arc)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = _mode_bits(full)
                z.writestr(info, data)
                count += 1
                if full.lower().endswith(EXEC_EXT):
                    execs.append(rel)
    print(f"MacMumble.zip built: {count} files -> {ZIP}")
    print("  executable (+x) launchers:", ", ".join(execs) if execs else "(none)")


if __name__ == "__main__":
    main()
