#!/usr/bin/env python3
"""Compatibility entry point for the Linux release builder.

Older maintenance scripts invoke ``app/_rebuild_zip.py``. Linux never shipped
the Windows-layout ``app/Mumble.zip`` that the old implementation expected, so
delegate to the deterministic port-level builder instead.
"""

from pathlib import Path
import runpy


BUILDER = Path(__file__).resolve().parents[1] / "build_release.py"

if __name__ == "__main__":
    runpy.run_path(str(BUILDER), run_name="__main__")
