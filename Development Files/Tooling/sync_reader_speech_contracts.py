#!/usr/bin/env python3
"""Synchronize the frozen Reader speech contract into maintained ports.

Windows remains the product-contract source. This helper copies only bounded,
platform-neutral Reader disclosure and source-documentation blocks so macOS and
Linux retain their platform-specific runtime seams. Checks compare normalized
newlines; every other byte inside a synchronized block remains authoritative.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Internal" / "app"
PORTS = (
    APP / "Ports" / "macOS" / "app",
    APP / "Ports" / "Linux" / "app",
)


@dataclass(frozen=True)
class ContractBlock:
    relative_path: Path
    start_marker: str
    end_marker: str


CONTRACT_BLOCKS = (
    ContractBlock(
        Path("webui/app.js"),
        "async function confirmReaderCloudUse(kind) {",
        "function readerBuildPane(text) {",
    ),
    ContractBlock(
        Path("webui_shell.py"),
        "    def reader_tts(self, text, model=None, voice=None, provider=None):",
        "        pid = provider or self.settings.get(\"reader_tts_provider\", \"openrouter\")",
    ),
    ContractBlock(
        Path("ai/tts_providers.py"),
        '"""TTS (text-to-speech) provider abstraction for Mumble.',
        "\n\nimport io",
    ),
    ContractBlock(
        Path("test_tts_providers.py"),
        '"""Tests for ai/tts_providers.py',
        "\n\nimport os",
    ),
    ContractBlock(
        Path("ai/__init__.py"),
        "            ordered = [model or route_decision.model]",
        "                attempts.append((pid, m, voice_id if primary else None, primary))",
    ),
    ContractBlock(
        Path("test_reader.py"),
        "# Monkey-patch get_tts_provider so we control the fallback order.",
        "    audio, ctype, meta = ai.synthesize_with_fallback(",
    ),
)


def read_normalized(path: Path) -> str:
    return path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


def extract_block(text: str, block: ContractBlock, path: Path) -> str:
    start = text.find(block.start_marker)
    if start < 0:
        raise RuntimeError(f"missing start marker in {path.relative_to(ROOT)}")
    if text.find(block.start_marker, start + 1) >= 0:
        raise RuntimeError(f"duplicate start marker in {path.relative_to(ROOT)}")
    end = text.find(block.end_marker, start)
    if end < 0:
        raise RuntimeError(f"missing end marker in {path.relative_to(ROOT)}")
    return text[start:end]


def expected_target(source: Path, target: Path, block: ContractBlock) -> str:
    source_text = read_normalized(source)
    target_text = read_normalized(target)
    authority = extract_block(source_text, block, source)
    current = extract_block(target_text, block, target)
    return target_text.replace(current, authority, 1)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    drift = []
    copied = []
    for block in CONTRACT_BLOCKS:
        source = APP / block.relative_path
        if not source.is_file():
            raise RuntimeError(f"missing authority: {source.relative_to(ROOT)}")
        for port in PORTS:
            target = port / block.relative_path
            if not target.is_file():
                raise RuntimeError(f"missing maintained port: {target.relative_to(ROOT)}")
            expected = expected_target(source, target, block)
            if read_normalized(target) == expected:
                continue
            relative = str(target.relative_to(ROOT))
            if args.check:
                drift.append(relative)
            else:
                target.write_bytes(expected.encode("utf-8"))
                copied.append(relative)

    if drift:
        print("Reader speech contract drift:")
        for relative in drift:
            print(f"  {relative}")
        return 1
    if copied:
        print(f"Synchronized {len(copied)} Reader speech contract blocks.")
    else:
        print("Reader speech contracts are synchronized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
