#!/usr/bin/env python3
"""Authoritative isolated runner for Mumble's Windows test modules."""

import os
from runner_core import main as run_main


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    return run_main(here=here)


if __name__ == "__main__":
    raise SystemExit(main())
