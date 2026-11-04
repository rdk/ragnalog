"""Exclusive OS file locks that the operating system releases when the process ends.

Used for the store's install step (one writer renames a set into place at a time,
across processes) and by the web app (one server per store). The lock is held on
the first byte of a small file that is never deleted: ``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere. Unlike ``O_EXCL`` marker files, a crashed process can
never leave a stale lock behind.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

__all__ = ["FileLock", "LockUnavailable"]

_POLL_S = 0.05


class LockUnavailable(OSError):
    """The file system does not support locking (some network shares)."""


class FileLock:
    """Exclusive, non-reentrant lock on ``path`` (created when missing).

    Two :class:`FileLock` objects on the same path exclude each other, also within
    one process.
    """

    def __init__(self, path: Path) -> None:
        """Prepare a lock on ``path``; nothing is opened until :meth:`acquire`."""
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        """Whether this object holds the lock."""
        return self._fd is not None

    def acquire(self, timeout: float = 0.0) -> bool:
        """Try to take the lock, waiting up to ``timeout`` seconds; ``False`` if another holds it.

        Raises:
            LockUnavailable: The file system cannot lock files.
            OSError: The lock file cannot be created.

        """
        if self._fd is not None:
            raise RuntimeError(f"{self.path} is already locked by this object")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + timeout
        try:
            while not _try_lock(fd, self.path):
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(_POLL_S)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        """Release the lock (no-op when not held)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int, path: Path) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except PermissionError:  # errno 13: another handle holds the byte
            return False
        except OSError as exc:
            raise LockUnavailable(f"cannot lock {path}: {exc}") from exc
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import errno
    import fcntl

    def _try_lock(fd: int, path: Path) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                return False
            raise LockUnavailable(f"cannot lock {path}: {exc}") from exc
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
