#!/usr/bin/env python3
"""Refresh Mumble.zip from the current source tree.

The zip mirrors the source under Mumble/Internal/app/. This re-packs every app
file from the live source (preserving the exact entry layout + the top-level
launchers), so the distributed zip always matches what's in this folder. Run via
sync_to_live.ps1, which ALSO pushes the same files into the live install in
place — so the two never drift apart (owner §26, 2026-06-13)."""
import os
import re
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))
ZIP = os.path.join(SRC, "Mumble.zip")
PREFIX = "Mumble/Internal/app/"

# NEW app files that must be added even though they aren't in the existing zip's
# entry list (the refresh loop only iterates pre-existing entries). Listed by
# their app-relative path; add here whenever a brand-new shipped module lands.
ENSURE = [
    "island_render.py", "transcription.py", "reader_store.py",
    "processing_route.py",
]


def main():
    if not os.path.exists(ZIP):
        print("Mumble.zip not found — nothing to refresh.")
        return
    new = ZIP + ".new"
    old = zipfile.ZipFile(ZIP)
    refreshed, dropped, added = 0, [], []
    written = set()
    with zipfile.ZipFile(new, "w", zipfile.ZIP_DEFLATED) as z:
        for info in old.infolist():
            n = info.filename
            if n.endswith("/"):
                z.writestr(info, b"")
                continue
            written.add(n)
            if n.startswith(PREFIX):
                # The dev source is AUTHORITATIVE for app files: refresh from it,
                # and DROP any zip entry whose source file is gone (so a deleted
                # file — e.g. the retired island_shell.py — actually leaves the
                # zip instead of lingering forever; the zip stays a true mirror).
                sp = os.path.join(SRC, n[len(PREFIX):].replace("/", os.sep))
                if os.path.exists(sp):
                    with open(sp, "rb") as f:
                        z.writestr(n, f.read())
                    refreshed += 1
                else:
                    dropped.append(n[len(PREFIX):])
            else:
                z.writestr(n, old.read(n))  # top-level launchers, untouched
        # add brand-new shipped files the old zip didn't know about
        for rel in ENSURE:
            n = PREFIX + rel
            sp = os.path.join(SRC, rel.replace("/", os.sep))
            if n not in written and os.path.exists(sp):
                with open(sp, "rb") as f:
                    z.writestr(n, f.read())
                added.append(rel)
    old.close()
    os.replace(new, ZIP)
    z2 = zipfile.ZipFile(ZIP)
    ver = re.search(r'VERSION = "([^"]+)"',
                    z2.read(PREFIX + "branding.py").decode("utf-8", "replace"))
    print(f"Mumble.zip refreshed: {refreshed} app files, "
          f"VERSION {ver.group(1) if ver else '?'}, {len(z2.namelist())} entries.")
    if added:
        print("  (added — new in source:", ", ".join(added), ")")
    if dropped:
        print("  (dropped — removed from source:", ", ".join(dropped), ")")


if __name__ == "__main__":
    main()
