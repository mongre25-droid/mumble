#!/usr/bin/env python3
"""Build deterministic, provenance-validated macOS source candidates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import zipfile


PORT_ROOT = Path(__file__).resolve().parent
APP_ROOT = PORT_ROOT / "app"
DEFAULT_OUTPUT = PORT_ROOT
ZIP_EPOCH = 315532800
SUPPORTED_ARCHITECTURES = ("arm64", "x86_64")
EXCLUDED_DIRECTORIES = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
    "__pycache__", "_test_logs",
}
EXCLUDED_NAMES = {
    ".DS_Store", "_rebuild_zip.py", "assets_gen.py", "brand_exe.py", "overlay.py",
    "requirements-dev.txt", "run_tests.py", "test-results.json",
}
EXECUTABLE_NAMES = {
    "Install Mumble.command", "Launch Mumble.command", "Uninstall Mumble.command",
}
TEXT_SUFFIXES = {
    ".command", ".css", ".html", ".js", ".json", ".md", ".py", ".sh",
    ".sql", ".txt",
}


def _repository_root() -> Path:
    for candidate in (PORT_ROOT, *PORT_ROOT.parents):
        if (candidate / "LICENSE").is_file() and (candidate / "Internal").is_dir():
            return candidate
    raise RuntimeError("repository root not found; build from a complete checkout")


def _provenance_module(repo_root: Path):
    path = repo_root / "Development Files" / "Tooling" / "release_provenance.py"
    if not path.is_file():
        raise RuntimeError(f"canonical provenance module is missing: {path}")
    spec = importlib.util.spec_from_file_location("mumble_release_provenance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canonical provenance module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version() -> str:
    text = (APP_ROOT / "branding.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match or not re.fullmatch(r"[A-Za-z0-9._-]+", match.group(1)):
        raise RuntimeError("branding.py contains no archive-safe VERSION")
    return match.group(1)


def _epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if not raw:
        return ZIP_EPOCH
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("SOURCE_DATE_EPOCH must be an integer") from exc
    if value < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must not be negative")
    return max(value, ZIP_EPOCH)


def _packaged_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES or path.name == "LICENSE":
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _checked_file(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"release source escapes its root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"release source contains a symlink: {current}")
    if not stat.S_ISREG(os.stat(candidate, follow_symlinks=False).st_mode):
        raise RuntimeError(f"release source is not a regular file: {candidate}")
    return candidate


def _source_members(repo_root: Path) -> dict[str, tuple[bytes, int]]:
    members: dict[str, tuple[bytes, int]] = {}
    tracked = set(subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, check=True,
        capture_output=True,
    ).stdout.decode("utf-8").rstrip("\0").split("\0"))

    def require_tracked(source: Path) -> Path:
        relative = source.relative_to(repo_root).as_posix()
        if relative not in tracked:
            raise RuntimeError(f"unexpected untracked macOS release source: {relative}")
        return source

    for name in EXECUTABLE_NAMES | {"READ ME FIRST.txt", "MAC_TESTING.md"}:
        source = require_tracked(_checked_file(PORT_ROOT / name, PORT_ROOT))
        members[name] = (_packaged_bytes(source), 0o755 if name in EXECUTABLE_NAMES else 0o644)

    for current, directories, files in os.walk(APP_ROOT):
        directories[:] = sorted(name for name in directories if name not in EXCLUDED_DIRECTORIES)
        for name in sorted(files):
            relative = (Path(current) / name).relative_to(APP_ROOT).as_posix()
            if (name in EXCLUDED_NAMES
                    or (name.startswith("test_") and name.endswith(".py"))
                    or name.endswith((".pyc", ".pyo"))):
                continue
            source = require_tracked(_checked_file(Path(current) / name, APP_ROOT))
            members[f"app/{relative}"] = (_packaged_bytes(source), 0o644)

    legal_sources = {
        "LICENSE": repo_root / "LICENSE",
        "THIRD_PARTY_NOTICES.md": repo_root / "Internal" / "app" / "THIRD_PARTY_NOTICES.md",
        "RELEASE-INVENTORY.json": repo_root / "Development Files" / "Legal" / "release-inventory.json",
        "DEPENDENCY-CLOSURE.json": repo_root / "Development Files" / "Legal" / "dependency-lock.json",
    }
    licence_root = repo_root / "Internal" / "app" / "licenses"
    if not licence_root.is_dir():
        raise RuntimeError(f"retained licence directory is missing: {licence_root}")
    for source in sorted(licence_root.rglob("*")):
        if source.is_file():
            relative = source.relative_to(licence_root).as_posix()
            legal_sources[f"licenses/{relative}"] = source
    for name, source in legal_sources.items():
        checked = require_tracked(_checked_file(source, repo_root))
        members[name] = (_packaged_bytes(checked), 0o644)

    if len(members) != len(set(members)):
        raise RuntimeError("duplicate macOS package member")
    return dict(sorted(members.items()))


def _entries(architecture: str) -> dict[str, tuple[bytes, int]]:
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ValueError(f"unsupported macOS architecture: {architecture}")
    repo_root = _repository_root()
    members = _source_members(repo_root)
    provenance = _provenance_module(repo_root)
    document = provenance.generate_release_provenance(
        repo_root=repo_root,
        platform="macos",
        architecture=architecture,
        package_format="zip",
        members=members,
        entrypoint="app/mumble_mac.py",
        installer="Install Mumble.command",
        uninstaller="Uninstall Mumble.command",
        assets=["app/assets/mumble.icns", "app/assets/mumble.png", "app/webui/mumble.png"],
        migrations_config=[
            "app/cloud_schema.sql", "app/settings.py", "app/update.py",
        ],
        evidence={
            "update": {
                "status": "unavailable", "reference": None,
                "reason": "No physical Mac update run is available.",
            },
            "rollback": {
                "status": "unavailable", "reference": None,
                "reason": "No physical Mac rollback run is available.",
            },
            "smoke": {
                "status": "passed",
                "reference": (
                    "https://github.com/mongre25-droid/mumble/"
                    "actions/runs/30721075874"
                ),
                "reason": (
                    "Automated package inspection only; not physical "
                    "installation."
                ),
            },
            "physical": {
                "status": "unavailable", "reference": None,
                "reason": "No real Mac is available in this environment.",
            },
        },
    )
    members["RELEASE-PROVENANCE.json"] = (document, 0o644)
    provenance.validate_release_provenance(document, repo_root=repo_root, members=members)
    return dict(sorted(members.items()))


def _write_zip(
    path: Path,
    root_name: str,
    members: dict[str, tuple[bytes, int]],
    epoch: int,
) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    stamp = time.gmtime(epoch)[:6]
    try:
        with os.fdopen(fd, "w+b") as raw:
            with zipfile.ZipFile(
                raw, "w", zipfile.ZIP_DEFLATED, compresslevel=9,
            ) as archive:
                for name, (data, mode) in members.items():
                    info = zipfile.ZipInfo(f"{root_name}/{name}", date_time=stamp)
                    info.create_system = 3
                    info.external_attr = ((0o100000 | mode) & 0xFFFF) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.flag_bits |= 0x800
                    archive.writestr(info, data, compresslevel=9)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _verify_zip(
    path: Path,
    root_name: str,
    members: dict[str, tuple[bytes, int]],
) -> None:
    expected = {f"{root_name}/{name}": value for name, value in members.items()}
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if [info.filename for info in infos] != list(expected):
            raise RuntimeError(f"macOS ZIP member mismatch: {path}")
        if archive.testzip() is not None:
            raise RuntimeError(f"macOS ZIP CRC verification failed: {path}")
        for info in infos:
            data, mode = expected[info.filename]
            if archive.read(info) != data:
                raise RuntimeError(f"macOS ZIP payload mismatch: {info.filename}")
            if (info.external_attr >> 16) & 0o777 != mode:
                raise RuntimeError(f"macOS ZIP mode mismatch: {info.filename}")


def _validate_written_provenance(path: Path, root_name: str) -> None:
    repo_root = _repository_root()
    provenance = _provenance_module(repo_root)
    with zipfile.ZipFile(path) as archive:
        members = {
            info.filename.removeprefix(root_name + "/"): (
                archive.read(info), (info.external_attr >> 16) & 0o7777)
            for info in archive.infolist()
            if info.filename.startswith(root_name + "/")
        }
    provenance.validate_release_provenance(
        members[provenance.PROVENANCE_NAME][0],
        repo_root=repo_root,
        members=members,
    )


def _write_checksum(candidate: Path) -> Path:
    checksum = candidate.with_suffix(candidate.suffix + ".sha256")
    expected = (
        f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  "
        f"{candidate.name}\n"
    ).encode("ascii")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{checksum.name}.", suffix=".tmp", dir=checksum.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, checksum)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    if checksum.read_bytes() != expected:
        raise RuntimeError(f"macOS checksum payload mismatch: {checksum}")
    return checksum


def build(output_dir: Path, architectures=SUPPORTED_ARCHITECTURES) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    version = _version()
    outputs: list[Path] = []
    for architecture in architectures:
        members = _entries(architecture)
        candidate = output_dir / f"Mumble-v{version}-macos-{architecture}.zip"
        _write_zip(candidate, "MacMumble", members, _epoch())
        _verify_zip(candidate, "MacMumble", members)
        _validate_written_provenance(candidate, "MacMumble")
        checksum = _write_checksum(candidate)
        outputs.extend((candidate, checksum))
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--architecture", choices=SUPPORTED_ARCHITECTURES, action="append")
    args = parser.parse_args(argv)
    architectures = tuple(args.architecture or SUPPORTED_ARCHITECTURES)
    for path in build(args.output_dir.resolve(), architectures):
        print(f"{path.name}: {path.stat().st_size} bytes")
    print(
        "Unsigned macOS source candidates verified; no physical execution "
        "or notarisation is claimed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
