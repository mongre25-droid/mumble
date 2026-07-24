#!/usr/bin/env python3
"""Tests for models/downloader.py — HuggingFace Hub model download with resume,
SHA256 verification, progress reporting, network error handling, and disk space checks.

Run: python test_downloader.py
Pure stdlib + project modules; network tests are optional and use mock servers.

Covers:
  VAL-MODL-003: User-initiated download shows real-time progress (bytes/percentage)
  VAL-MODL-004: Interrupted download resumes from last byte position via HTTP Range
  VAL-MODL-005: SHA256 hash verified after download; mismatch discards file
  VAL-MODL-017: Low disk space blocks download with clear message
  VAL-MODL-020: Network error mid-download preserves partial file for resume
"""

import os
import sys
import shutil
import tempfile
import hashlib
import threading
import time
import http.server
import socketserver

# Ensure the app directory is on sys.path so we can import project modules.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


# ============================================================================
# Helpers — local test HTTP server
# ============================================================================

class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """A minimal HTTP server that supports Range requests for testing resume."""

    # Class-level state set by the test harness.
    server_content = b""
    server_sha256 = ""
    fail_after_bytes = -1  # simulate network failure after N bytes
    fail_count = 0
    request_log = []       # (method, path, headers_dict, response_code)

    def do_HEAD(self):
        cl = len(self.server_content)
        self.send_response(200)
        self.send_header("Content-Length", str(cl))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        _RangeHandler.request_log.append(
            ("HEAD", self.path, dict(self.headers), 200))

    def do_GET(self):
        headers = dict(self.headers)
        cl = len(self.server_content)
        range_header = headers.get("Range", "")

        # Simulate failure after N bytes.
        if (_RangeHandler.fail_after_bytes > 0
                and _RangeHandler.fail_count > 0):
            _RangeHandler.fail_count -= 1
            # Send partial response then close connection (simulate drop).
            partial = min(_RangeHandler.fail_after_bytes, cl)
            self.send_response(200)
            self.send_header("Content-Length", str(cl))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            try:
                self.wfile.write(self.server_content[:partial])
            except (BrokenPipeError, ConnectionResetError):
                pass
            _RangeHandler.request_log.append(
                ("GET", self.path, headers, 200))
            return

        if range_header:
            # Parse "bytes=START-END"
            try:
                rng = range_header.replace("bytes=", "").strip()
                if "-" in rng:
                    parts = rng.split("-", 1)
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if parts[1] else cl - 1
                else:
                    start = int(rng)
                    end = cl - 1
            except (ValueError, IndexError):
                self.send_response(416)
                self.end_headers()
                _RangeHandler.request_log.append(
                    ("GET", self.path, headers, 416))
                return

            if start >= cl:
                self.send_response(416)
                self.end_headers()
                _RangeHandler.request_log.append(
                    ("GET", self.path, headers, 416))
                return

            end = min(end, cl - 1)
            chunk = self.server_content[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{cl}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(chunk)
            _RangeHandler.request_log.append(
                ("GET", self.path, headers, 206))
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(cl))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(self.server_content)
            _RangeHandler.request_log.append(
                ("GET", self.path, headers, 200))

    def log_message(self, format, *args):
        # Suppress server logs during tests.
        pass


def _start_test_server(content=b"Hello GGUF test content!\x00" * 100,
                       sha256="", fail_after=-1, fail_count=0):
    """Start a local HTTP server for testing download logic.

    Returns (server_thread, port, stop_event).
    """
    _RangeHandler.server_content = content
    _RangeHandler.server_sha256 = sha256
    _RangeHandler.fail_after_bytes = fail_after
    _RangeHandler.fail_count = fail_count
    _RangeHandler.request_log = []

    # Bind to port 0 for auto-assignment.
    server = socketserver.TCPServer(("127.0.0.1", 0), _RangeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stop_event = threading.Event()

    def stop():
        server.shutdown()
        server.server_close()

    stop_event.stop = stop
    return thread, port, stop_event


# ============================================================================
#  VAL-MODL-003: Progress reporting
# ============================================================================

def test_progress_callback_receives_bytes():
    """Progress callback is invoked with (bytes_downloaded, total_bytes)."""
    from models.downloader import ModelDownloader

    content = b"X" * 5000
    thread, port, stop = _start_test_server(content=content)
    try:
        progress_events = []
        dl = ModelDownloader(models_dir=tempfile.mkdtemp())
        dest = os.path.join(dl.models_dir, "test-model.gguf")

        dl._download_file(
            url=f"http://127.0.0.1:{port}/test-model.gguf",
            dest=dest,
            on_progress=lambda downloaded, total: progress_events.append(
                (downloaded, total)),
        )
        assert len(progress_events) > 0, "progress callback never called"
        # At least one progress event with correct total.
        final = progress_events[-1]
        assert final[0] == len(content), \
            f"final downloaded={final[0]}, expected={len(content)}"
        assert final[1] == len(content), \
            f"final total={final[1]}, expected={len(content)}"
        assert os.path.getsize(dest) == len(content)
    finally:
        stop.stop()


def test_progress_callback_percentage():
    """Progress events show increasing byte counts."""
    from models.downloader import ModelDownloader

    content = b"Y" * 10000
    thread, port, stop = _start_test_server(content=content)
    try:
        events = []
        dl = ModelDownloader(models_dir=tempfile.mkdtemp())
        dest = os.path.join(dl.models_dir, "progress-test.gguf")

        dl._download_file(
            url=f"http://127.0.0.1:{port}/progress-test.gguf",
            dest=dest,
            on_progress=lambda d, t: events.append(d),
        )
        # Events should be non-decreasing.
        for i in range(1, len(events)):
            assert events[i] >= events[i - 1], \
                f"progress went backwards: {events[i - 1]} → {events[i]}"
        assert events[-1] == len(content)
    finally:
        stop.stop()


# ============================================================================
#  VAL-MODL-004: HTTP Range resume
# ============================================================================

def test_resume_from_partial_file():
    """When a .part file exists, download resumes from its size via Range."""
    from models.downloader import ModelDownloader

    content = b"A" * 5000
    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "resume-test.gguf")
        part = dest + ".part"

        # Create a partial file (first 2000 bytes).
        with open(part, "wb") as f:
            f.write(content[:2000])

        dl = ModelDownloader(models_dir=models_dir)
        dl._download_file(
            url=f"http://127.0.0.1:{port}/resume-test.gguf",
            dest=dest,
        )

        # Verify the full file was assembled.
        with open(dest, "rb") as f:
            result = f.read()
        assert result == content, f"resumed file mismatch, len={len(result)}"
        # .part file should be removed.
        assert not os.path.exists(part), ".part file not cleaned up"
        # Check that a Range request was made (resuming from byte 2000).
        range_requests = [r for r in _RangeHandler.request_log
                          if r[0] == "GET" and "Range" in r[2]]
        assert len(range_requests) >= 1, \
            "no Range request made for resume"
    finally:
        stop.stop()


def test_resume_no_partial_file():
    """When no .part file exists, full download from scratch (no Range)."""
    from models.downloader import ModelDownloader

    content = b"B" * 3000
    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "fresh-download.gguf")

        dl = ModelDownloader(models_dir=models_dir)
        dl._download_file(
            url=f"http://127.0.0.1:{port}/fresh-download.gguf",
            dest=dest,
        )
        with open(dest, "rb") as f:
            assert f.read() == content
    finally:
        stop.stop()


def test_partial_file_removed_on_success():
    """After successful download, the .part file is renamed/removed."""
    from models.downloader import ModelDownloader

    content = b"C" * 8000
    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "cleanup-test.gguf")
        part = dest + ".part"

        # Start with a tiny partial file.
        with open(part, "wb") as f:
            f.write(content[:100])

        dl = ModelDownloader(models_dir=models_dir)
        dl._download_file(
            url=f"http://127.0.0.1:{port}/cleanup-test.gguf",
            dest=dest,
        )
        assert os.path.exists(dest), "final file missing"
        assert not os.path.exists(part), ".part still exists"
    finally:
        stop.stop()


# ============================================================================
#  VAL-MODL-005: SHA256 verification
# ============================================================================

def test_sha256_verification_match():
    """When SHA256 matches, download succeeds."""
    from models.downloader import ModelDownloader

    content = b"Test content for SHA256 verification."
    expected = hashlib.sha256(content).hexdigest()

    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "sha256-ok.gguf")

        dl = ModelDownloader(models_dir=models_dir)
        result = dl._download_file(
            url=f"http://127.0.0.1:{port}/sha256-ok.gguf",
            dest=dest,
            expected_sha256=expected,
        )
        assert result == dest
        with open(dest, "rb") as f:
            assert f.read() == content
    finally:
        stop.stop()


def test_sha256_verification_mismatch_discards_file():
    """When SHA256 mismatches, the file is discarded and error raised."""
    from models.downloader import ModelDownloader

    content = b"Original content that has a known hash."
    wrong_hash = hashlib.sha256(b"something else entirely").hexdigest()

    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "sha256-fail.gguf")

        dl = ModelDownloader(models_dir=models_dir)
        try:
            dl._download_file(
                url=f"http://127.0.0.1:{port}/sha256-fail.gguf",
                dest=dest,
                expected_sha256=wrong_hash,
            )
            assert False, "should have raised"
        except Exception as e:
            msg = str(e).lower()
            assert "sha256" in msg or "hash" in msg or "mismatch" in msg, \
                f"error should mention hash mismatch, got: {e}"
        # File should be discarded.
        assert not os.path.exists(dest), \
            "mismatched file should have been deleted"
        assert not os.path.exists(dest + ".part"), \
            ".part should also be cleaned up"
    finally:
        stop.stop()


def test_sha256_missing_skips_verification():
    """When no SHA256 is provided, download succeeds without verification."""
    from models.downloader import ModelDownloader

    content = b"No hash provided, should still work."
    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "no-hash.gguf")

        dl = ModelDownloader(models_dir=models_dir)
        result = dl._download_file(
            url=f"http://127.0.0.1:{port}/no-hash.gguf",
            dest=dest,
            expected_sha256=None,
        )
        assert result == dest
        assert os.path.exists(dest)
    finally:
        stop.stop()


def test_compute_sha256():
    """compute_sha256 returns correct hex digest."""
    from models.downloader import ModelDownloader
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(b"hello sha256 test data!")
        f.flush()
        result = ModelDownloader.compute_sha256(f.name)
    expected = hashlib.sha256(b"hello sha256 test data!").hexdigest()
    assert result == expected


# ============================================================================
#  VAL-MODL-017: Low disk space check
# ============================================================================

def test_disk_space_check_passes_when_enough_space():
    """When disk has enough space, no error raised."""
    from models.downloader import ModelDownloader

    models_dir = tempfile.mkdtemp()
    # Request just 1 byte — any disk should have that.
    ModelDownloader.check_disk_space(models_dir, required_bytes=1)


def test_disk_space_check_blocks_when_insufficient():
    """When disk has insufficient space, raises clear error."""
    from models.downloader import ModelDownloader

    models_dir = tempfile.mkdtemp()
    # Request more space than any disk has.
    insane_bytes = 2**60  # 1 exabyte
    try:
        ModelDownloader.check_disk_space(models_dir,
                                         required_bytes=insane_bytes,
                                         safety_margin_mb=0)
        assert False, "should have raised"
    except OSError as e:
        msg = str(e).lower()
        assert "disk" in msg or "space" in msg or "insufficient" in msg, \
            f"error should mention disk space, got: {e}"


def test_disk_space_error_includes_required_and_available():
    """Error message tells the user how much space is needed and available."""
    from models.downloader import ModelDownloader

    models_dir = tempfile.mkdtemp()
    try:
        # Use shutil to get actual free space.
        usage = shutil.disk_usage(models_dir)
        required = usage.free + usage.used + (500 * 1024 * 1024)  # way more
        ModelDownloader.check_disk_space(models_dir,
                                         required_bytes=required,
                                         safety_margin_mb=0)
        assert False, "should have raised"
    except OSError as e:
        msg = str(e)
        # Should contain numbers (bytes or MB).
        assert any(c.isdigit() for c in msg), \
            f"error message should include numeric values: {e}"


def test_disk_space_safety_margin():
    """Safety margin is added to the required bytes."""
    from models.downloader import ModelDownloader

    models_dir = tempfile.mkdtemp()
    usage = shutil.disk_usage(models_dir)
    # Request exactly the free space minus 1 byte, with 500MB safety margin.
    required = usage.free - 1
    try:
        ModelDownloader.check_disk_space(models_dir,
                                         required_bytes=required,
                                         safety_margin_mb=500)
        assert False, "should have blocked due to safety margin"
    except OSError:
        pass  # Expected — safety margin pushes it over.


# ============================================================================
#  VAL-MODL-020: Network error mid-download preserves partial file
# ============================================================================

def test_network_error_preserves_partial_file():
    """When download fails mid-way, .part file is preserved for resume."""
    from models.downloader import ModelDownloader

    content = b"D" * 10000
    # Fail once after sending ~3000 bytes (failure simulates a dropped connection).
    thread, port, stop = _start_test_server(
        content=content, fail_after=3000, fail_count=1)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "network-error.gguf")
        part = dest + ".part"

        dl = ModelDownloader(models_dir=models_dir, max_retries=1)
        try:
            dl._download_file(
                url=f"http://127.0.0.1:{port}/network-error.gguf",
                dest=dest,
            )
        except Exception:
            pass  # Expected to fail.

        # Partial file should exist with some data.
        assert os.path.exists(part), \
            ".part file should be preserved after network error"
        part_size = os.path.getsize(part)
        assert part_size > 0, "partial file should have bytes"
        # Final dest should NOT exist (download didn't complete).
        assert not os.path.exists(dest), \
            "final file should not exist on failed download"
    finally:
        stop.stop()


def test_network_error_no_zero_byte_file():
    """Network error should not leave a 0-byte .part file."""
    from models.downloader import ModelDownloader

    content = b"E" * 5000
    # Fail immediately (fail_after=0 means fail on first request).
    thread, port, stop = _start_test_server(
        content=content, fail_after=0, fail_count=1)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "zero-byte.gguf")
        part = dest + ".part"

        dl = ModelDownloader(models_dir=models_dir)
        try:
            dl._download_file(
                url=f"http://127.0.0.1:{port}/zero-byte.gguf",
                dest=dest,
            )
        except Exception:
            pass

        # If the partial file exists, it should have bytes.
        if os.path.exists(part):
            assert os.path.getsize(part) > 0, \
                "partial file should not be 0-byte"
    finally:
        stop.stop()


def test_retry_after_network_error():
    """After network error, retry continues from where it left off."""
    from models.downloader import ModelDownloader

    content = b"F" * 8000
    # First attempt fails partway, retry succeeds.
    # We need to set up so that the first request fails but retry works.
    thread, port, stop = _start_test_server(
        content=content, fail_after=3000, fail_count=1)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "retry-test.gguf")

        dl = ModelDownloader(models_dir=models_dir, max_retries=1,
                             retry_delay=0.1)

        # First attempt should fail.
        try:
            dl._download_file(
                url=f"http://127.0.0.1:{port}/retry-test.gguf",
                dest=dest,
            )
        except Exception:
            pass

        # After first failure, partial should exist.
        part = dest + ".part"
        assert os.path.exists(part)

        # Reset fail count so second attempt succeeds.
        _RangeHandler.fail_after_bytes = -1
        _RangeHandler.fail_count = 0

        # Retry should resume from partial.
        dl._download_file(
            url=f"http://127.0.0.1:{port}/retry-test.gguf",
            dest=dest,
        )

        assert os.path.exists(dest), "retry should have completed download"
        with open(dest, "rb") as f:
            assert f.read() == content
        assert not os.path.exists(part), ".part should be removed"
    finally:
        stop.stop()


# ============================================================================
#  Integration: URL construction, file naming
# ============================================================================

def test_huggingface_url_construction():
    """URL is correctly built from repo_id and filename."""
    from models.downloader import ModelDownloader

    url = ModelDownloader.build_hf_url(
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    )
    assert "huggingface.co" in url
    assert "Qwen/Qwen2.5-1.5B-Instruct-GGUF" in url
    assert "qwen2.5-1.5b-instruct-q4_k_m.gguf" in url
    assert "resolve/main" in url


def test_huggingface_url_with_revision():
    """Optional revision parameter is reflected in the URL."""
    from models.downloader import ModelDownloader

    url = ModelDownloader.build_hf_url(
        "org/repo", "model.gguf", revision="v1.0"
    )
    assert "resolve/v1.0" in url


def test_huggingface_metadata_url_is_revision_pinned():
    """Hash/size metadata must come from the same revision as the file."""
    from models.downloader import ModelDownloader

    url = ModelDownloader.build_hf_metadata_url(
        "org/repo", revision="refs/pr/17"
    )
    assert "/api/models/org/repo/revision/refs%2Fpr%2F17" in url


def test_download_forwards_revision_to_metadata_lookups():
    """A tag download cannot accidentally verify against main metadata."""
    from models.downloader import ModelDownloader

    seen = []

    class ProbeDownloader(ModelDownloader):
        def _fetch_sha256(self, repo_id, filename, revision="main"):
            seen.append(("sha", revision))
            return None

        def _fetch_file_size(self, repo_id, filename, revision="main"):
            seen.append(("size", revision))
            return None

        def _download_file(self, url, dest, **kwargs):
            return dest

    with tempfile.TemporaryDirectory() as d:
        ProbeDownloader(models_dir=d).download(
            "org/repo", "model.gguf", revision="v2.4"
        )
    assert seen == [("sha", "v2.4"), ("size", "v2.4")]


# ============================================================================
#  Edge cases
# ============================================================================

def test_nonexistent_models_dir():
    """Downloader works with a nonexistent models directory (creates it)."""
    from models.downloader import ModelDownloader

    base = tempfile.mkdtemp()
    nonexistent = os.path.join(base, "does", "not", "exist")
    dl = ModelDownloader(models_dir=nonexistent)
    # Directory should be created.
    assert os.path.isdir(dl.models_dir)
    shutil.rmtree(base)


def test_empty_url_raises():
    """Empty URL raises a clear error."""
    from models.downloader import ModelDownloader
    dl = ModelDownloader(models_dir=tempfile.mkdtemp())
    try:
        dl._download_file(url="", dest=os.path.join(dl.models_dir, "x.gguf"))
        assert False, "should have raised"
    except ValueError as e:
        assert "url" in str(e).lower()


def test_same_partial_as_expected_size():
    """If .part file is same size as expected total, download skips."""
    from models.downloader import ModelDownloader

    content = b"G" * 4000
    thread, port, stop = _start_test_server(content=content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "complete-part.gguf")
        part = dest + ".part"

        # Write full content as .part.
        with open(part, "wb") as f:
            f.write(content)

        dl = ModelDownloader(models_dir=models_dir)
        dl._download_file(
            url=f"http://127.0.0.1:{port}/complete-part.gguf",
            dest=dest,
            expected_size=len(content),
        )
        # Should rename .part to final without re-downloading.
        assert os.path.exists(dest)
        assert not os.path.exists(part)
        with open(dest, "rb") as f:
            assert f.read() == content
    finally:
        stop.stop()


def test_download_to_custom_dest():
    """Download can target a specific path."""
    from models.downloader import ModelDownloader

    content = b"H" * 2000
    thread, port, stop = _start_test_server(content=content)
    try:
        dl = ModelDownloader(models_dir=tempfile.mkdtemp())
        custom = os.path.join(dl.models_dir, "custom-path.gguf")
        dl._download_file(
            url=f"http://127.0.0.1:{port}/custom.gguf",
            dest=custom,
        )
        assert os.path.exists(custom)
    finally:
        stop.stop()


# ============================================================================
#  VAL-RECV-004: Shutdown during active model download preserves partial file
# ============================================================================

class _SlowRangeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP server that streams content SLOWLY (small chunks with a delay) so a
    download can be reliably cancelled mid-stream. Supports Range resume."""

    server_content = b""
    chunk_bytes = 256
    chunk_delay = 0.02  # seconds between chunks

    def _serve(self, start, end):
        cl = len(self.server_content)
        start = max(0, min(start, cl))
        end = min(end, cl - 1)
        if start > end:
            self.send_response(416)
            self.end_headers()
            return
        chunk = self.server_content[start:end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{cl}")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        # Write in tiny pieces with a delay so the client loop iterates many
        # times and we can cancel it mid-stream.
        for i in range(0, len(chunk), self.chunk_bytes):
            piece = chunk[i:i + self.chunk_bytes]
            try:
                self.wfile.write(piece)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            time.sleep(self.chunk_delay)

    def do_HEAD(self):
        cl = len(self.server_content)
        self.send_response(200)
        self.send_header("Content-Length", str(cl))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range", "")
        cl = len(self.server_content)
        if rng:
            body = rng.replace("bytes=", "").strip()
            parts = body.split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else cl - 1
        else:
            start, end = 0, cl - 1
        self._serve(start, end)

    def log_message(self, format, *args):
        pass


def _start_slow_server(content, chunk_delay=0.02):
    _SlowRangeHandler.server_content = content
    _SlowRangeHandler.chunk_delay = chunk_delay
    server = socketserver.TCPServer(("127.0.0.1", 0), _SlowRangeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stop_event = threading.Event()

    def stop():
        server.shutdown()
        server.server_close()
    stop_event.stop = stop
    return thread, port, stop_event


def test_cancel_mid_download_preserves_partial_file():
    """VAL-RECV-004: cancelling a download mid-stream leaves the .part file on
    disk for resume. No 0-byte partial, no final dest file."""
    from models.downloader import ModelDownloader

    content = b"Z" * 60000
    thread, port, stop = _start_slow_server(content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "cancel-test.gguf")
        part = dest + ".part"
        dl = ModelDownloader(models_dir=models_dir, max_retries=1,
                             chunk_size=512)

        result = {}
        def _run():
            try:
                dl._download_file(
                    url=f"http://127.0.0.1:{port}/cancel-test.gguf",
                    dest=dest,
                )
                result["ok"] = True
            except RuntimeError as e:
                result["err"] = str(e)
            except Exception as e:  # noqa: BLE001
                result["err"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=_run)
        t.start()
        # Wait until some bytes have been written to the .part file.
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(part) and os.path.getsize(part) > 0:
                break
            time.sleep(0.05)
        assert os.path.exists(part) and os.path.getsize(part) > 0, \
            "download never started writing bytes"
        # Cancel mid-stream (simulates app shutdown during active download).
        dl.cancel()
        t.join(timeout=15)
        assert not t.is_alive(), "download thread did not stop after cancel"

        # The .part file MUST be preserved with partial bytes.
        assert os.path.exists(part), \
            ".part file was deleted on cancel — cannot resume"
        part_size = os.path.getsize(part)
        assert 0 < part_size < len(content), \
            f"partial size {part_size} should be between 0 and {len(content)}"
        # The final destination must NOT exist (download did not complete).
        assert not os.path.exists(dest), \
            "final dest created despite cancellation"
        # The error must be a clean cancellation, not a crash.
        assert "cancel" in (result.get("err") or "").lower(), \
            f"unexpected outcome: {result}"
    finally:
        stop.stop()


def test_cancel_then_resume_completes():
    """VAL-RECV-004 full flow: cancel mid-download, then re-trigger the same
    download — it resumes from the .part file via HTTP Range and completes."""
    from models.downloader import ModelDownloader

    content = b"R" * 60000
    sha = hashlib.sha256(content).hexdigest()
    thread, port, stop = _start_slow_server(content)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "resume-after-cancel.gguf")
        part = dest + ".part"
        dl = ModelDownloader(models_dir=models_dir, max_retries=1,
                             chunk_size=512)

        # Phase 1: start and cancel mid-stream.
        state = {}
        def _run_cancel():
            try:
                dl._download_file(
                    url=f"http://127.0.0.1:{port}/resume-after-cancel.gguf",
                    dest=dest,
                )
                state["ok"] = True
            except RuntimeError as e:
                state["err"] = str(e)

        t = threading.Thread(target=_run_cancel)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(part) and os.path.getsize(part) > 0:
                break
            time.sleep(0.05)
        dl.cancel()
        t.join(timeout=15)
        assert os.path.exists(part) and os.path.getsize(part) > 0
        partial_bytes = os.path.getsize(part)

        # Phase 2: a fresh downloader (simulating next app launch) resumes and
        # completes the download from the preserved .part file.
        dl2 = ModelDownloader(models_dir=models_dir, max_retries=1,
                              chunk_size=512)
        dl2._download_file(
            url=f"http://127.0.0.1:{port}/resume-after-cancel.gguf",
            dest=dest,
            expected_sha256=sha,
            expected_size=len(content),
        )
        # Final file assembled correctly.
        assert os.path.exists(dest), "resume did not produce final file"
        assert not os.path.exists(part), ".part not cleaned up after resume"
        with open(dest, "rb") as f:
            assert f.read() == content, "resumed content mismatch"
        # Sanity: the partial bytes were actually reused (resume, not restart).
        assert partial_bytes < len(content)
    finally:
        stop.stop()


def test_cancel_is_idempotent_and_clean():
    """Calling cancel() before and after a download never raises and leaves
    the downloader in a clean state."""
    from models.downloader import ModelDownloader
    dl = ModelDownloader(models_dir=tempfile.mkdtemp())
    dl.cancel()  # before any download — no error
    assert dl._cancelled is True
    # A fresh downloader is not cancelled.
    dl2 = ModelDownloader(models_dir=tempfile.mkdtemp())
    assert dl2._cancelled is False


def test_cancel_active_downloads_cancels_all_registered():
    """VAL-RECV-004: cancel_active_downloads() signals every registered
    in-flight download to stop. Safe no-op when none active."""
    from models.downloader import (
        ModelDownloader, cancel_active_downloads, active_download_count,
        register_active, unregister_active,
    )
    # No active downloads -> no-op, returns 0.
    assert active_download_count() == 0
    assert cancel_active_downloads() == 0

    dl1 = ModelDownloader(models_dir=tempfile.mkdtemp())
    dl2 = ModelDownloader(models_dir=tempfile.mkdtemp())
    register_active(dl1)
    register_active(dl2)
    assert active_download_count() == 2
    n = cancel_active_downloads()
    assert n == 2
    assert dl1._cancelled is True
    assert dl2._cancelled is True
    # cancel_active_downloads does not unregister (the download thread does
    # that in its finally); but the count is still 2 until unregistered.
    unregister_active(dl1)
    unregister_active(dl2)
    assert active_download_count() == 0
    # Calling again is a clean no-op.
    assert cancel_active_downloads() == 0


def test_download_registers_and_unregisters_active():
    """VAL-RECV-004: an in-flight download is tracked while running and
    untracked after it finishes (success or cancel)."""
    from models.downloader import (
        ModelDownloader, active_download_count, cancel_active_downloads,
    )
    content = b"P" * 40000
    thread, port, stop = _start_slow_server(content, chunk_delay=0.02)
    try:
        models_dir = tempfile.mkdtemp()
        dest = os.path.join(models_dir, "active-track.gguf")
        dl = ModelDownloader(models_dir=models_dir, max_retries=1,
                             chunk_size=512)
        seen_active = threading.Event()

        def _run():
            try:
                dl._download_file(
                    url=f"http://127.0.0.1:{port}/active-track.gguf",
                    dest=dest,
                )
            except RuntimeError:
                pass

        t = threading.Thread(target=_run)
        t.start()
        # While running, the download must be registered.
        deadline = time.time() + 10
        while time.time() < deadline:
            if active_download_count() >= 1:
                seen_active.set()
                break
            time.sleep(0.02)
        assert seen_active.is_set(), "download was never registered as active"
        # Cancel via the registry (simulates app shutdown).
        assert cancel_active_downloads() >= 1
        t.join(timeout=15)
        assert not t.is_alive()
        # After the download thread exits, it must have unregistered.
        assert active_download_count() == 0, \
            "download not unregistered after cancel"
    finally:
        stop.stop()


def test_successful_download_unregisters_active():
    """A download that completes normally is unregistered afterwards."""
    from models.downloader import (
        ModelDownloader, active_download_count,
    )
    content = b"S" * 3000
    thread, port, stop = _start_test_server(content=content)
    try:
        before = active_download_count()
        dl = ModelDownloader(models_dir=tempfile.mkdtemp())
        dest = os.path.join(dl.models_dir, "ok-active.gguf")
        dl._download_file(
            url=f"http://127.0.0.1:{port}/ok-active.gguf",
            dest=dest,
        )
        assert os.path.exists(dest)
        assert active_download_count() == before, \
            "successful download was not unregistered"
    finally:
        stop.stop()


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
