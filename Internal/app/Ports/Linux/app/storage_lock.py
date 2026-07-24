"""Tiny cross-process lock for local JSON read/modify/write stores."""

import contextlib
import os
import secrets
import time


def _identity(stat_result):
    return (getattr(stat_result, "st_dev", None),
            getattr(stat_result, "st_ino", None))


def _same_path_identity(path, expected):
    """Whether *path* still names the exact inode observed earlier."""
    try:
        return _identity(os.lstat(path)) == expected
    except OSError:
        return False


@contextlib.contextmanager
def exclusive_file_lock(data_path, timeout=3.0, stale=30.0):
    """Yield True while ``data_path + '.lock'`` is exclusively owned.

    POSIX uses a kernel ``flock`` (automatically released after crashes).
    Windows uses an identity-checked exclusive-create marker and reclaims a
    crashed writer after ``stale`` seconds. On timeout, yield False so callers
    fail closed instead of performing the race this guard exists to prevent.
    """
    lock_path = str(data_path) + ".lock"

    # POSIX advisory locks are kernel-owned and automatically released when a
    # process crashes. Keep the tiny lock file in place: unlinking it while a
    # waiter has the old inode open can create two independently locked inodes.
    if os.name == "posix":
        import fcntl

        fd = None
        acquired = False
        started = time.monotonic()
        try:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                yield False
                return
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() - started >= timeout:
                        break
                    time.sleep(0.02)
                except OSError:
                    break
            yield acquired
        finally:
            if fd is not None:
                if acquired:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    os.close(fd)
                except OSError:
                    pass
        return

    # Windows fallback: an exclusive-create marker with identity/token checks.
    # (fcntl is unavailable there, and Windows denies replacing an open file.)
    fd = None
    owned_identity = None
    owner_token = secrets.token_hex(16).encode("ascii")
    started = time.monotonic()
    while fd is None:
        try:
            fd = os.open(
                lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            owned_identity = _identity(os.fstat(fd))
            os.write(fd, owner_token)
        except FileExistsError:
            try:
                observed = os.lstat(lock_path)
                if time.time() - observed.st_mtime > stale:
                    # Re-check identity immediately before unlinking. Without
                    # this, a successor could replace the stale marker between
                    # stat() and remove(), and we would delete the fresh lock.
                    expected = _identity(observed)
                    if _same_path_identity(lock_path, expected):
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
            # Only unlink the marker if it still names OUR inode. A stale-lock
            # contender may have replaced it while this context was running;
            # unconditional removal would erase that successor's live lock.
            removed = False
            if _same_path_identity(lock_path, owned_identity):
                try:
                    os.remove(lock_path)
                    removed = True
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
            # Windows may refuse unlinking an open descriptor. The Linux port
            # unlinks above atomically; this close-then-recheck fallback keeps
            # cross-platform offline tests and source runs from leaking locks.
            if not removed and _same_path_identity(lock_path, owned_identity):
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
