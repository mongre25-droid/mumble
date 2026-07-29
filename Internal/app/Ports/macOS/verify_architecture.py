#!/usr/bin/env python3
"""Fail closed when a macOS package is built with an unsupported Python arch."""

from __future__ import annotations

import argparse
import platform
import struct
import sys


SUPPORTED = {"arm64": "Apple Silicon", "x86_64": "Intel 64-bit"}


def verify(machine=None, pointer_bits=None):
    machine = str(machine or platform.machine()).lower()
    aliases = {"amd64": "x86_64", "aarch64": "arm64"}
    machine = aliases.get(machine, machine)
    bits = int(pointer_bits or struct.calcsize("P") * 8)
    if machine not in SUPPORTED:
        return False, f"Unsupported macOS architecture: {machine or 'unknown'}"
    if bits != 64:
        return False, "Mumble requires a 64-bit Python runtime on macOS."
    return True, f"{SUPPORTED[machine]} ({machine}) source package"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine")
    args = parser.parse_args(argv)
    ok, message = verify(args.machine)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
