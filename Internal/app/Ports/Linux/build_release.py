#!/usr/bin/env python3
"""Build reproducible, self-verified Mumble Linux release archives.

The Linux port is installed from source into a per-user virtual environment, so
the portable release artifacts are a ``tar.gz`` (preferred on Linux because it
always preserves executable modes) and a compatibility ``zip`` whose Unix mode
metadata also marks the installer scripts executable.

Build metadata is deliberately independent of checkout mtimes. Set
``SOURCE_DATE_EPOCH`` when a release pipeline has an authoritative timestamp;
otherwise the ZIP epoch is used. Both archives are written atomically and read
back before they are accepted.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
import time
import zipfile


PORT_ROOT = Path(__file__).resolve().parent
APP_ROOT = PORT_ROOT / "app"
DEFAULT_OUTPUT = PORT_ROOT / "dist"
ZIP_EPOCH = 315532800  # 1980-01-01 00:00:00 UTC (ZIP's minimum timestamp).

# Release contents are an allowlist, not "everything except known junk". This
# prevents a new audit JSON, cache, test fixture, Windows copy, or local secret
# from silently entering a published archive. Keep this list aligned with the
# import closure of mumble_linux.py and webui_shell.py.
RUNTIME_PATHS = (
    "requirements.txt",
    "ai/__init__.py",
    "ai/base.py",
    "ai/constitution.py",
    "ai/stt_providers.py",
    "ai/transport.py",
    "ai/tts_providers.py",
    "ai/providers/__init__.py",
    "ai/providers/anthropic.py",
    "ai/providers/cerebras.py",
    "ai/providers/deepseek.py",
    "ai/providers/groq.py",
    "ai/providers/local.py",
    "ai/providers/openai.py",
    "ai/providers/openrouter.py",
    "assets/mumble.png",
    "autostart.py",
    "bindings.py",
    "branding.py",
    "clipboard.py",
    "cloud_sync.py",
    "context_store.py",
    "favorites.py",
    "experimental/__init__.py",
    "experimental/system_search/__init__.py",
    "experimental/system_search/engine.py",
    "experimental/system_search/ui.css",
    "experimental/system_search/ui.js",
    "foreign_boost.py",
    "formatting.py",
    "history.py",
    "islamic_terms.py",
    "island_render.py",
    "local_engine.py",
    "meeting.py",
    "meeting_diarise.py",
    "meeting_store.py",
    "model_free.py",
    "models/__init__.py",
    "models/backend.py",
    "models/cache.py",
    "models/downloader.py",
    "models/manager.py",
    "models/registry.py",
    "mumble_linux.py",
    "overlay_linux.py",
    "pipeline/__init__.py",
    "pipeline/grammar.py",
    "pipeline/stage_cleanup.py",
    "pipeline/stage_formatting.py",
    "pipeline/stage_grammar.py",
    "pipeline/stage_punctuation.py",
    "platform/__init__.py",
    "platform/linux_fallback.py",
    "presets.py",
    "prompt_constitution.py",
    "prompt_history.py",
    "prompt_template_registry.py",
    "reader_parser.py",
    "reader_store.py",
    "recording_limits.py",
    "settings.py",
    "stats.py",
    "storage_lock.py",
    "tips.py",
    "transcription.py",
    "update.py",
    "webui/app.css",
    "webui/app.js",
    "webui/enhanced.css",
    "webui/index.html",
    "webui/mumble.png",
    "webui/remaster.css",
    "webui/system-search-loader.js",
    "webui_shell.py",
)

FORBIDDEN_RUNTIME_PATHS = {
    "ai.py",              # retired compatibility guard
    "app_window.py",      # Windows/Tk window
    "assets/mumble.ico",  # Windows icon container
    "assets_gen.py",      # development-time generator
    "brand_exe.py",       # Windows PE resource editor
    "mumble.py",          # Windows controller copy
    "overlay.py",         # Windows/Tk overlay
    "prompt_memory.py",   # retired continuity store
    "platform/macos_audio.py",
    "platform/windows_dml.py",
    "ui.py",              # unused Tk toolkit; Linux uses WebKitGTK
}


def _repository_root() -> Path:
    """Find the checkout root so every release necessarily includes LICENSE."""
    for candidate in (PORT_ROOT, *PORT_ROOT.parents):
        if (candidate / "LICENSE").is_file() and (candidate / "Internal").is_dir():
            return candidate
    raise RuntimeError("repository LICENSE not found; build from a complete checkout")


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


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_source(path: Path, allowed_root: Path) -> Path:
    """Return a regular, non-symlinked source contained by ``allowed_root``.

    Checking only the leaf is insufficient: a symlinked parent directory can
    redirect an apparently allowlisted path outside the release tree.
    """
    root = allowed_root.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"release source escapes its root: {path}") from exc

    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise RuntimeError(f"release source contains a symlink: {current}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"release source is missing or escapes its root: {path}") from exc
    try:
        mode = os.stat(candidate, follow_symlinks=False).st_mode
    except OSError as exc:
        raise RuntimeError(f"release source is unreadable: {path}") from exc
    if not stat.S_ISREG(mode):
        raise RuntimeError(f"release source is not a regular file: {path}")
    return candidate


def _runtime_files() -> list[tuple[str, bytes, int]]:
    """Return sorted ``(archive path, bytes, mode)`` runtime entries."""
    rows: list[tuple[str, bytes, int]] = []
    repository_root = _repository_root()
    fixed = [
        (_checked_source(PORT_ROOT / "install.sh", PORT_ROOT),
         "install.sh", 0o755),
        (_checked_source(PORT_ROOT / "uninstall.sh", PORT_ROOT),
         "uninstall.sh", 0o755),
        (_checked_source(APP_ROOT / "READ ME FIRST.txt", APP_ROOT),
         "READ ME FIRST.txt", 0o644),
        (_checked_source(repository_root / "LICENSE", repository_root),
         "LICENSE", 0o644),
    ]
    for source, name, mode in fixed:
        data = source.read_bytes()
        if name.endswith(".sh") and b"\r" in data:
            raise RuntimeError(f"{source} contains CR bytes; Linux scripts must use LF")
        rows.append((name, data, mode))

    for relative in RUNTIME_PATHS:
        source = _checked_source(APP_ROOT / relative, APP_ROOT)
        rows.append((f"app/{relative}", source.read_bytes(), 0o644))

    names = [name for name, _data, _mode in rows]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate paths in release input")
    forbidden = {f"app/{name}" for name in FORBIDDEN_RUNTIME_PATHS}
    leaked = sorted(forbidden.intersection(names))
    if leaked:
        raise RuntimeError("non-Linux files entered release: " + ", ".join(leaked))
    return sorted(rows, key=lambda row: row[0])


def _entries(version: str) -> list[tuple[str, bytes, int]]:
    runtime = _runtime_files()
    manifest = {
        "schema": 1,
        "application": "Mumble voice-to-text",
        "version": version,
        "platform": "linux",
        "python": ">=3.12",
        "entrypoint": "app/mumble_linux.py",
        "installer": "install.sh",
        "uninstaller": "uninstall.sh",
        "files": [
            {"path": name, "sha256": _digest(data), "mode": f"{mode:04o}"}
            for name, data, mode in runtime
        ],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return sorted(
        runtime + [("RELEASE-MANIFEST.json", manifest_bytes, 0o644)],
        key=lambda row: row[0],
    )


def _tar_bytes(root_name: str, entries: list[tuple[str, bytes, int]], epoch: int,
               destination: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
                with tarfile.open(
                        fileobj=zipped, mode="w",
                        format=tarfile.PAX_FORMAT) as archive:
                    for name, data, mode in entries:
                        info = tarfile.TarInfo(f"{root_name}/{name}")
                        info.size = len(data)
                        info.mode = mode
                        info.mtime = epoch
                        info.uid = info.gid = 0
                        info.uname = info.gname = "root"
                        archive.addfile(info, io.BytesIO(data))
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _zip_bytes(root_name: str, entries: list[tuple[str, bytes, int]], epoch: int,
               destination: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    stamp = time.gmtime(epoch)[:6]
    try:
        with os.fdopen(fd, "w+b") as raw:
            with zipfile.ZipFile(
                    raw, "w", compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9) as archive:
                for name, data, mode in entries:
                    info = zipfile.ZipInfo(
                        f"{root_name}/{name}", date_time=stamp)
                    info.create_system = 3  # Unix
                    info.external_attr = ((0o100000 | mode) & 0xFFFF) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.flag_bits |= 0x800  # UTF-8 names
                    archive.writestr(info, data, compresslevel=9)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _verify_tar(path: Path, root_name: str,
                entries: list[tuple[str, bytes, int]]) -> None:
    expected = {f"{root_name}/{name}": (data, mode) for name, data, mode in entries}
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        actual_names = [member.name for member in members]
        if actual_names != list(expected):
            raise RuntimeError(f"tar member mismatch: {path}")
        for member in members:
            data, mode = expected[member.name]
            handle = archive.extractfile(member)
            if not member.isfile() or handle is None or handle.read() != data:
                raise RuntimeError(f"tar payload mismatch: {member.name}")
            if member.mode & 0o777 != mode:
                raise RuntimeError(f"tar mode mismatch: {member.name}")


def _verify_zip(path: Path, root_name: str,
                entries: list[tuple[str, bytes, int]]) -> None:
    expected = {f"{root_name}/{name}": (data, mode) for name, data, mode in entries}
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if [info.filename for info in infos] != list(expected):
            raise RuntimeError(f"zip member mismatch: {path}")
        if archive.testzip() is not None:
            raise RuntimeError(f"zip CRC verification failed: {path}")
        for info in infos:
            data, mode = expected[info.filename]
            if archive.read(info) != data:
                raise RuntimeError(f"zip payload mismatch: {info.filename}")
            if (info.external_attr >> 16) & 0o777 != mode:
                raise RuntimeError(f"zip mode mismatch: {info.filename}")


def build(output_dir: Path) -> list[Path]:
    version = _version()
    root_name = f"Mumble-Linux-{version}"
    entries = _entries(version)
    epoch = _epoch()
    output_dir.mkdir(parents=True, exist_ok=True)

    tar_path = output_dir / f"{root_name}.tar.gz"
    zip_path = output_dir / f"{root_name}.zip"
    _tar_bytes(root_name, entries, epoch, tar_path)
    _zip_bytes(root_name, entries, epoch, zip_path)
    _verify_tar(tar_path, root_name, entries)
    _verify_zip(zip_path, root_name, entries)

    sums_path = output_dir / "SHA256SUMS"
    sums = "".join(
        f"{_digest(path.read_bytes())}  {path.name}\n"
        for path in (tar_path, zip_path)
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{sums_path.name}.", suffix=".tmp", dir=output_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
            handle.write(sums)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, sums_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return [tar_path, zip_path, sums_path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"artifact directory (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    outputs = build(args.output_dir.resolve())
    for path in outputs:
        print(f"{path.name}: {path.stat().st_size} bytes")
    print("Release archives verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
