"""Tiny cross-process lock for local JSON read/modify/write stores."""

import contextlib
import os
import time


@contextlib.contextmanager
def exclusive_file_lock(data_path, timeout=3.0, stale=30.0):
    """Yield True while ``data_path + '.lock'`` is exclusively owned.

    The marker is portable across Windows/macOS/Linux and is only held around
    short local JSON transactions.  A crashed writer's marker is reclaimed
    after ``stale`` seconds.  On timeout, yield False so callers fail closed
    instead of performing the very race this guard exists to prevent.
    """
    lock_path = str(data_path) + ".lock"
    fd = None
    started = time.monotonic()
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            # Windows can report EACCES instead of EEXIST while another
            # process is closing/removing the marker. Treat that brief hand-off
            # as contention, but keep the same bounded timeout and fail closed.
            try:
                if time.time() - os.path.getmtime(lock_path) > stale:
                    os.remove(lock_path)
                    continue
            except OSError:
                pass
            if time.monotonic() - started >= timeout:
                break
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield fd is not None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(lock_path)
            except OSError:
                pass
