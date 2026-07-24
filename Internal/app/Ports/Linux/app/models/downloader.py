#!/usr/bin/env python3
"""Model Downloader — HuggingFace Hub download with resume, SHA256 verification,
progress reporting, network error handling, and disk space checks.

Provides ModelDownloader: downloads GGUF model files from HuggingFace Hub using
plain urllib (no heavy dependencies). Supports:
  - HTTP Range-based resume from partial (.part) files
  - SHA256 verification (against expected hash from HF API or registry)
  - Real-time progress reporting via callback (bytes/percentage)
  - Low disk space blocking before any bytes are transferred
  - Network error preservation of partial files for retry

Pure stdlib + optional ModelManager/ModelRegistry integration. No huggingface_hub
dependency needed.
"""

import hashlib
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import json
import socket

# Import branding for the default models directory. Guarded so the module
# still imports on unusual platforms.
try:
    from branding import MODELS_DIR
except ImportError:
    MODELS_DIR = os.path.join(os.path.expanduser("~"), "Mumble", "models")

# Optional integration with ModelManager and ModelRegistry.
try:
    from models.manager import ModelManager  # noqa: F401
except ImportError:
    ModelManager = None  # type: ignore[assignment]

try:
    from models.registry import ModelRegistry  # noqa: F401
except ImportError:
    ModelRegistry = None  # type: ignore[assignment]

# ============================================================================
# Constants
# ============================================================================

HF_API_BASE = "https://huggingface.co"
HF_RESOLVE_BASE = "https://huggingface.co"
HF_API_MODELS = "https://huggingface.co/api/models"

# Default buffer size for streaming downloads (64 KiB).
_DEFAULT_CHUNK_SIZE = 64 * 1024

# Minimum interval between progress callbacks (seconds).
_PROGRESS_INTERVAL = 0.1

# Default safety margin for disk space checks (MB).
_DEFAULT_SAFETY_MARGIN_MB = 500

# Default user agent for HTTP requests.
_USER_AGENT = "Mumble-ModelDownloader/1.0"


# ============================================================================
# Active-download registry (VAL-RECV-004)
# ============================================================================
# Every in-flight ModelDownloader registers itself here so the app controller
# can cleanly interrupt all active downloads on shutdown. The .part file is
# preserved by _download_file's cancel path, enabling resume on next launch.
import threading as _threading

_ACTIVE_DOWNLOADS = set()
_ACTIVE_LOCK = _threading.Lock()


def register_active(downloader):
    """Track an in-flight downloader so it can be cancelled on shutdown."""
    with _ACTIVE_LOCK:
        _ACTIVE_DOWNLOADS.add(downloader)


def unregister_active(downloader):
    """Stop tracking a downloader (download finished or raised)."""
    with _ACTIVE_LOCK:
        _ACTIVE_DOWNLOADS.discard(downloader)


def cancel_active_downloads():
    """Cancel every in-flight model download (VAL-RECV-004).

    Called by the app controller on shutdown so a model download in progress is
    cleanly interrupted. Each download's .part file is preserved on disk for
    resume on the next launch. Safe to call when no download is active (no-op).
    Returns the number of downloads signalled to cancel.
    """
    with _ACTIVE_LOCK:
        active = list(_ACTIVE_DOWNLOADS)
    n = 0
    for dl in active:
        try:
            dl.cancel()
            n += 1
        except Exception:
            pass
    return n


def active_download_count():
    """Return the number of currently in-flight downloads."""
    with _ACTIVE_LOCK:
        return len(_ACTIVE_DOWNLOADS)


# ============================================================================
# ModelDownloader
# ============================================================================

class ModelDownloader:
    """Downloads GGUF model files from HuggingFace Hub with resume, verification,
    and progress reporting.

    Usage:
        dl = ModelDownloader(manager=some_mgr, registry=some_reg)
        path = dl.download(
            repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
            filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
            on_progress=lambda downloaded, total: print(f"{downloaded}/{total}"),
        )
        # Model is now at `path`, verified, and registered with manager.

    The downloader can also be used standalone (without manager/registry):
        dl = ModelDownloader(models_dir="/custom/path")
        path = dl._download_file(
            url="https://huggingface.co/...",
            dest="/custom/path/model.gguf",
            expected_sha256="abc123...",
        )
    """

    def __init__(self, models_dir=None, manager=None, registry=None,
                 max_retries=3, retry_delay=2.0, chunk_size=_DEFAULT_CHUNK_SIZE):
        """Create a ModelDownloader.

        Args:
            models_dir: Directory where models are stored. Defaults to
                        branding.MODELS_DIR.
            manager: Optional ModelManager instance for status tracking.
            registry: Optional ModelRegistry instance for metadata lookups.
            max_retries: How many times to retry on transient failures.
            retry_delay: Seconds to wait between retries.
            chunk_size: Bytes per read during streaming download.
        """
        self.models_dir = models_dir or MODELS_DIR
        self.manager = manager
        self.registry = registry
        self.max_retries = max(1, int(max_retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.chunk_size = max(1, int(chunk_size))
        self._cancelled = False

        # Ensure the models directory exists.
        os.makedirs(self.models_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download(self, repo_id, filename, revision="main",
                 expected_sha256=None, quantisation=None,
                 on_progress=None):
        """Download a model file from HuggingFace Hub.

        This is the main public entry point. It:
          1. Looks up model metadata in the registry (if available).
          2. Checks disk space before any bytes are transferred.
          3. Builds the HF URL from repo_id and filename.
          4. Downloads with resume, progress, and SHA256 verification.
          5. Updates ModelManager status on completion.

        Args:
            repo_id: HuggingFace repo ID (e.g. "Qwen/Qwen2.5-1.5B-Instruct-GGUF").
            filename: The GGUF filename to download.
            revision: Branch, tag, or commit hash. Default "main".
            expected_sha256: Expected SHA256 hex digest. If None, attempts to
                             fetch from HuggingFace API.
            quantisation: Quantisation level (for registry lookup). Optional.
            on_progress: Callable(bytes_downloaded, total_bytes) called during
                         download. May be called many times.

        Returns:
            The absolute path to the downloaded (and verified) GGUF file.

        Raises:
            OSError: If disk space is insufficient.
            ValueError: If repo_id or filename is empty.
            RuntimeError: If download fails after all retries or SHA256 mismatches.
        """
        if not repo_id or not filename:
            raise ValueError("repo_id and filename are required")
        if not isinstance(filename, str) or (
            filename != os.path.basename(filename)
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise ValueError("filename must be a plain filename, not a path")
        if not filename.lower().endswith(".gguf"):
            raise ValueError("model filename must end with .gguf")

        dest = os.path.join(self.models_dir, filename)

        # Update manager status if available.
        model_name = self._model_name_from_filename(filename)
        if self.manager:
            try:
                self.manager.set_status(model_name, "downloading")
            except Exception:
                pass  # Manager integration is best-effort.

        # Resolve SHA256 if not explicitly provided.
        if expected_sha256 is None:
            expected_sha256 = self._fetch_sha256(
                repo_id, filename, revision=revision
            )

        # Compute total expected size from the registry or HF API.
        expected_size = self._fetch_file_size(
            repo_id, filename, revision=revision
        )

        # Check disk space before transferring any bytes.
        self._ensure_disk_space(expected_size, filename)

        # Build the download URL.
        url = self.build_hf_url(repo_id, filename, revision=revision)

        # Download with resume and verification.
        try:
            result_path = self._download_file(
                url=url,
                dest=dest,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                on_progress=on_progress,
            )
        except Exception:
            # On failure, leave status as-is (partial file preserved).
            raise

        # Update manager: mark as available.
        if self.manager:
            try:
                self.manager.set_status(model_name, "available")
            except Exception:
                pass

        # Trigger rediscovery so the newly-downloaded model appears.
        if self.manager:
            try:
                self.manager.discover()
            except Exception:
                pass

        return result_path

    def cancel(self):
        """Cancel an in-progress download. Call from another thread.

        The download will stop at the next chunk boundary and clean up.
        """
        self._cancelled = True

    # ------------------------------------------------------------------
    # Internal: download file
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_download_url(url):
        value = str(url or "").strip()
        try:
            parsed = urllib.parse.urlparse(value)
            if (parsed.scheme.lower() not in ("http", "https")
                    or not parsed.hostname):
                raise ValueError
            parsed.port
        except (TypeError, ValueError):
            raise ValueError("download URL must use http:// or https://") from None
        return value

    def _download_file(self, url, dest, expected_sha256=None,
                       expected_size=None, on_progress=None):
        """Download a file from `url` to `dest` with resume and verification.

        This is the low-level download engine. It handles:
          - Resume from .part file via HTTP Range
          - Progress reporting
          - Retry on transient failures
          - SHA256 verification
          - .part file cleanup

        Args:
            url: Full URL to download from.
            dest: Final destination path.
            expected_sha256: Expected SHA256 hex, or None to skip verification.
            expected_size: Expected file size in bytes, or None if unknown.
            on_progress: Callable(bytes_downloaded, total_bytes).

        Returns:
            The destination path on success.

        Raises:
            ValueError: If url is empty.
            RuntimeError: If download fails after all retries or hash mismatches.
        """
        url = self._validate_download_url(url)

        part = dest + ".part"
        self._cancelled = False

        # Determine starting byte offset from existing .part file.
        existing_bytes = 0
        if os.path.exists(part):
            existing_bytes = os.path.getsize(part)
            # If the partial file matches the expected total size, we're done.
            if expected_size and existing_bytes == expected_size:
                self._finalise_part(part, dest, expected_sha256)
                return dest
            if expected_size and existing_bytes > expected_size:
                # Never promote an oversized partial.  This occurs when a
                # previous server ignored Range and the old implementation
                # appended a full response to the partial file.
                os.remove(part)
                existing_bytes = 0
        elif os.path.exists(dest):
            # Final file already exists — verify and return.
            if expected_sha256:
                actual = self.compute_sha256(dest)
                if actual.lower() == expected_sha256.lower():
                    return dest
                # Hash mismatch on existing file — delete and re-download.
                os.remove(dest)

        # Track this download so the app controller can cancel it on shutdown
        # (VAL-RECV-004). The .part file is preserved on cancel for resume.
        register_active(self)
        try:
            # Retry loop for transient failures.
            last_error = None
            for attempt in range(self.max_retries):
                if self._cancelled:
                    raise RuntimeError("Download cancelled")

                try:
                    self._do_download(
                        url=url,
                        part=part,
                        offset=existing_bytes,
                        expected_size=expected_size,
                        on_progress=on_progress,
                    )
                    # Download complete — verify and finalise.
                    self._finalise_part(part, dest, expected_sha256)
                    return dest

                except (urllib.error.URLError, urllib.error.HTTPError,
                        socket.timeout, ConnectionError, OSError) as e:
                    last_error = e
                    # Don't retry on 4xx client errors (except 416/429).
                    if isinstance(e, urllib.error.HTTPError):
                        code = e.code
                        if code not in (416, 429) and 400 <= code < 500:
                            raise RuntimeError(
                                f"Download failed (HTTP {code}): {e}"
                            ) from e

                    # Re-check existing bytes in case the partial file changed.
                    if os.path.exists(part):
                        existing_bytes = os.path.getsize(part)

                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise RuntimeError(
                        f"Download failed after {self.max_retries} attempts: "
                        f"{last_error}"
                    ) from last_error

            # Should not reach here.
            raise RuntimeError(
                f"Download failed: {last_error}"
            )
        finally:
            unregister_active(self)

    def _do_download(self, url, part, offset=0, expected_size=None,
                     on_progress=None):
        """Core streaming download with Range support.

        Streams the remote file to `part`, appending from `offset`.
        Calls `on_progress` periodically with (total_downloaded, total_size).
        """
        url = self._validate_download_url(url)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", _USER_AGENT)

        # If we have a partial file, request only the remaining bytes.
        if offset > 0:
            req.add_header("Range", f"bytes={offset}-")

        # First, do a HEAD or get Content-Length from the response.
        # We open the URL and read headers before streaming the body.
        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code == 416:
                # Range not satisfiable — the partial file may be complete.
                # Check if we already have the full content.
                if expected_size and os.path.exists(part):
                    if os.path.getsize(part) == expected_size:
                        return  # Already complete.
                # Otherwise, start fresh.
                req = urllib.request.Request(url)
                req.add_header("User-Agent", _USER_AGENT)
                resp = urllib.request.urlopen(req, timeout=30)
                offset = 0
            else:
                raise

        # A number of CDNs ignore Range and reply 200 with the entire file.  If
        # we append that body to an existing partial, the resulting GGUF is
        # silently corrupt whenever no SHA was available.  Only append when the
        # response explicitly confirms the requested starting offset.
        content_range = resp.headers.get("Content-Range", "")
        if offset > 0:
            range_start = None
            try:
                units, span = content_range.split(None, 1)
                range_start = int(span.split("-", 1)[0])
                if units.lower() != "bytes":
                    range_start = None
            except (AttributeError, ValueError, IndexError):
                range_start = None
            status = getattr(resp, "status", None)
            if range_start != offset or (status is not None and status != 206):
                try:
                    resp.close()
                except Exception:
                    pass
                req = urllib.request.Request(url)
                req.add_header("User-Agent", _USER_AGENT)
                resp = urllib.request.urlopen(req, timeout=30)
                offset = 0
                content_range = resp.headers.get("Content-Range", "")

        # Determine total size.
        total_size = None
        if content_range:
            # e.g. "bytes 2000-4999/5000"
            try:
                total_size = int(content_range.rsplit("/", 1)[-1])
            except (ValueError, IndexError):
                pass
        if total_size is None:
            cl = resp.headers.get("Content-Length", "")
            if cl and cl.isdigit():
                total_size = int(cl) + offset  # total = already have + remaining

        # Fall back to expected_size if available.
        if total_size is None:
            total_size = expected_size

        # Open the partial file for appending.
        mode = "ab" if offset > 0 else "wb"
        try:
            with open(part, mode) as f:
                downloaded = offset
                last_progress = 0.0
                while True:
                    if self._cancelled:
                        f.flush()
                        raise RuntimeError("Download cancelled")

                    chunk = resp.read(self.chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Throttle progress callbacks.
                    now = time.monotonic()
                    if on_progress and (now - last_progress >= _PROGRESS_INTERVAL):
                        on_progress(downloaded, total_size or downloaded)
                        last_progress = now
        finally:
            try:
                resp.close()
            except Exception:
                pass

        # Final progress callback.
        if on_progress:
            on_progress(downloaded, total_size or downloaded)

        # Enforce the independently sourced expected size as well as the
        # server-declared length.  A self-consistent but wrong Content-Length
        # must not allow a truncated model through when no SHA is available.
        if expected_size is not None and downloaded != expected_size:
            raise OSError(
                f"Invalid download size: got {downloaded} of "
                f"{expected_size} expected bytes"
            )

        # If we know the server-declared total size, verify we got everything.
        if total_size and downloaded != total_size:
            # OSError is treated as transient by the outer retry loop.  A short
            # clean EOF is just as retryable as a socket reset.
            raise OSError(
                f"Invalid download size: got {downloaded} of {total_size} bytes"
            )

    def _finalise_part(self, part, dest, expected_sha256=None):
        """Verify SHA256 and rename .part to final destination.

        If SHA256 verification fails, the .part file is deleted.
        """
        # Verify SHA256 if we have an expected hash.
        if expected_sha256:
            actual = self.compute_sha256(part)
            if actual.lower() != expected_sha256.lower():
                # Hash mismatch — discard the file.
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise RuntimeError(
                    f"SHA256 verification failed.\n"
                    f"  Expected: {expected_sha256.lower()}\n"
                    f"  Got:      {actual.lower()}\n"
                    f"The downloaded file has been discarded."
                )

        # Atomic replacement avoids a data-loss window where the previous good
        # destination was deleted before a rename failure.
        os.replace(part, dest)

    # ------------------------------------------------------------------
    # SHA256 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_sha256(filepath):
        """Compute the SHA256 hex digest of a file.

        Reads the file in chunks to avoid loading large model files into memory.
        """
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(_DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # HF API helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_hf_url(repo_id, filename, revision="main"):
        """Build the HuggingFace Hub download URL for a file.

        Example result:
          https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf
        """
        repo = urllib.parse.quote(str(repo_id).strip(), safe="/")
        rev = urllib.parse.quote(str(revision).strip(), safe="")
        name = urllib.parse.quote(str(filename).strip(), safe="")
        return f"{HF_RESOLVE_BASE}/{repo}/resolve/{rev}/{name}"

    @staticmethod
    def build_hf_metadata_url(repo_id, revision="main"):
        """Build the revision-pinned HuggingFace model metadata URL."""
        repo = urllib.parse.quote(str(repo_id).strip(), safe="/")
        rev = urllib.parse.quote(str(revision or "main").strip(), safe="")
        return f"{HF_API_MODELS}/{repo}/revision/{rev}"

    def _fetch_sha256(self, repo_id, filename, revision="main"):
        """Try to fetch the expected SHA256 from the HuggingFace Hub API.

        Uses https://huggingface.co/api/models/{repo_id} which returns
        file metadata including lfs.sha256 for each sibling.

        Returns the SHA256 hex string, or None if unavailable.
        """
        # First, check the registry.
        if self.registry:
            try:
                # Look up the model by repo_id.
                for model_id, meta in self.registry.MODELS.items():
                    if meta.get("huggingface_id") == repo_id:
                        # The registry may not have SHA256 — but at least
                        # we confirmed the model exists.
                        break
            except Exception:
                pass

        # Try the HuggingFace API.
        api_url = self.build_hf_metadata_url(repo_id, revision)
        try:
            req = urllib.request.Request(api_url)
            req.add_header("User-Agent", _USER_AGENT)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None  # API unavailable — skip verification.

        # Search siblings for the matching filename.
        siblings = data.get("siblings", [])
        for sib in siblings:
            if sib.get("rfilename") == filename:
                lfs = sib.get("lfs", {})
                sha = lfs.get("sha256")
                if sha:
                    return sha
                # Some endpoints use a flat "sha256" field.
                sha = sib.get("sha256")
                if sha:
                    return sha

        return None  # File not found in API response.

    def _fetch_file_size(self, repo_id, filename, revision="main"):
        """Try to fetch the expected file size from the HF API or registry.

        Returns the size in bytes, or None if unavailable.
        """
        # Query the exact file metadata.  Registry ``size_gb`` values describe
        # a model family/typical quantisation and are not exact byte counts;
        # using them as a resume boundary can promote a truncated or oversized
        # quantisation file.
        api_url = self.build_hf_metadata_url(repo_id, revision)
        try:
            req = urllib.request.Request(api_url)
            req.add_header("User-Agent", _USER_AGENT)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        siblings = data.get("siblings", [])
        for sib in siblings:
            if sib.get("rfilename") == filename:
                size = sib.get("size")
                if size:
                    return size
                lfs = sib.get("lfs", {})
                size = lfs.get("size")
                if size:
                    return size

        return None

    # ------------------------------------------------------------------
    # Disk space
    # ------------------------------------------------------------------

    @staticmethod
    def check_disk_space(path, required_bytes, safety_margin_mb=_DEFAULT_SAFETY_MARGIN_MB):
        """Check that the disk containing `path` has enough free space.

        Args:
            path: A path on the disk to check (doesn't need to exist).
            required_bytes: The number of bytes needed for the download.
            safety_margin_mb: Additional safety margin in megabytes. Default 500.

        Raises:
            OSError: If the available space is insufficient, with a clear
                     message including how much is needed and available.
        """
        # Resolve to an existing directory for disk_usage.
        check_dir = path
        if os.path.isfile(check_dir):
            check_dir = os.path.dirname(check_dir)
        # Walk up until we find an existing directory.
        while check_dir and not os.path.isdir(check_dir):
            parent = os.path.dirname(check_dir)
            if parent == check_dir:
                break
            check_dir = parent

        if not check_dir or not os.path.isdir(check_dir):
            # Fallback: use the current working directory.
            check_dir = os.getcwd()

        try:
            usage = shutil.disk_usage(check_dir)
        except OSError:
            # Can't check — let the download proceed and fail later.
            return

        margin_bytes = safety_margin_mb * 1024 * 1024
        total_needed = required_bytes + margin_bytes

        if usage.free < total_needed:
            free_mb = usage.free / (1024 * 1024)
            needed_mb = total_needed / (1024 * 1024)
            required_mb = required_bytes / (1024 * 1024)
            margin_mb = safety_margin_mb

            raise OSError(
                f"Insufficient disk space for model download.\n"
                f"  Required: {required_mb:.0f} MB (model file)\n"
                f"  Safety margin: {margin_mb:.0f} MB\n"
                f"  Total needed: {needed_mb:.0f} MB\n"
                f"  Available: {free_mb:.0f} MB\n"
                f"  Drive: {check_dir}\n"
                f"Please free up space or use a smaller model."
            )

    def _ensure_disk_space(self, expected_size, filename):
        """Check disk space before download, using expected_size if known.

        If expected_size is unknown, skip the check (download may still fail
        later if disk fills up).
        """
        if expected_size is None:
            # We don't know the size — try the HF API.
            return  # Best-effort: let the download proceed.

        dest = os.path.join(self.models_dir, filename)
        part = dest + ".part"
        existing = os.path.getsize(part) if os.path.exists(part) else 0
        remaining = max(0, expected_size - existing)

        if remaining > 0:
            self.check_disk_space(dest, remaining)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _model_name_from_filename(filename):
        """Extract a human-readable model name from a GGUF filename.

        Uses the same parsing logic as ModelManager._parse_gguf_filename.
        Falls back to the base filename without extension.
        """
        try:
            from models.manager import _parse_gguf_filename
            info = _parse_gguf_filename(filename)
            return info.get("name", os.path.splitext(filename)[0])
        except (ImportError, Exception):
            return os.path.splitext(filename)[0]
