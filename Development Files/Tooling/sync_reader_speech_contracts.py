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
import os
from pathlib import Path
import stat
import sys
import tempfile


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


@dataclass(frozen=True)
class PlannedWrite:
    target: Path
    expected: bytes
    original: bytes
    relative: str
    mode: int


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


def require_unredirected(path: Path, label: str) -> Path:
    absolute = path.absolute()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"missing {label}: {absolute}") from exc
    if resolved != absolute:
        raise RuntimeError(
            f"{label} is outside the exact approved maintained port set/root: "
            f"{absolute} -> {resolved}"
        )
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise RuntimeError(f"{label} must not be a symlink or junction: {absolute}")
    return resolved


def validate_layout() -> None:
    app_root = require_unredirected(APP, "Windows authority root")
    expected_ports = (
        APP / "Ports" / "macOS" / "app",
        APP / "Ports" / "Linux" / "app",
    )
    if tuple(port.absolute() for port in PORTS) != tuple(
        port.absolute() for port in expected_ports
    ):
        raise RuntimeError("targets are outside the exact approved maintained port set")

    resolved_ports = tuple(
        require_unredirected(port, "approved maintained port") for port in PORTS
    )
    if any(not port.is_relative_to(app_root) for port in resolved_ports):
        raise RuntimeError("approved maintained port resolves outside the authority root")

    relative_paths = []
    for block in CONTRACT_BLOCKS:
        relative = block.relative_path
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RuntimeError(f"unsafe Reader contract path: {relative}")
        relative_paths.append(relative)
    if len(set(relative_paths)) != len(relative_paths):
        raise RuntimeError("duplicate Reader contract target path")

    for relative in relative_paths:
        source = require_unredirected(APP / relative, "Windows authority file")
        if not source.is_file() or source.stat().st_nlink != 1:
            raise RuntimeError(f"authority file is not an exclusive regular file: {relative}")
        if not source.is_relative_to(app_root):
            raise RuntimeError(f"authority file escapes its root: {relative}")
        for port, port_root in zip(PORTS, resolved_ports):
            target = require_unredirected(port / relative, "maintained target file")
            if not target.is_file() or target.stat().st_nlink != 1:
                raise RuntimeError(
                    f"maintained target is not an exclusive regular file: {relative}"
                )
            if not target.is_relative_to(port_root):
                raise RuntimeError(f"maintained target escapes its port root: {relative}")


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


def stage_bytes(target: Path, payload: bytes, label: str, mode: int) -> Path:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f"{target.name}.reader-sync-{label}-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def remove_staged(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        path.unlink(missing_ok=True)


def apply_plan(plan: list[PlannedWrite]) -> None:
    staged = []
    try:
        for item in plan:
            replacement = None
            restoration = None
            try:
                replacement = stage_bytes(
                    item.target, item.expected, "replacement", item.mode
                )
                restoration = stage_bytes(
                    item.target, item.original, "restoration", item.mode
                )
            except BaseException:
                if replacement is not None:
                    remove_staged(replacement)
                if restoration is not None:
                    remove_staged(restoration)
                raise
            staged.append((item, replacement, restoration))
    except BaseException as exc:
        for _item, replacement, restoration in staged:
            remove_staged(replacement)
            remove_staged(restoration)
        raise RuntimeError("Reader speech synchronization staging failed") from exc

    applied = []
    try:
        for item, replacement, restoration in staged:
            os.replace(replacement, item.target)
            applied.append((item, restoration))
    except BaseException as exc:
        rollback_errors = []
        for item, restoration in reversed(applied):
            try:
                os.replace(restoration, item.target)
            except BaseException as rollback_exc:
                rollback_errors.append(f"{item.relative}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                "Reader speech synchronization failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        raise RuntimeError(
            "Reader speech synchronization failed; all changed targets were restored"
        ) from exc
    finally:
        for _item, replacement, restoration in staged:
            remove_staged(replacement)
            remove_staged(restoration)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    validate_layout()
    drift = []
    plan = []
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
                plan.append(
                    PlannedWrite(
                        target=target,
                        expected=expected.encode("utf-8"),
                        original=target.read_bytes(),
                        relative=relative,
                        mode=stat.S_IMODE(target.stat().st_mode),
                    )
                )

    if drift:
        print("Reader speech contract drift:")
        for relative in drift:
            print(f"  {relative}")
        return 1
    apply_plan(plan)
    if plan:
        print(f"Synchronized {len(plan)} Reader speech contract blocks.")
    else:
        print("Reader speech contracts are synchronized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
