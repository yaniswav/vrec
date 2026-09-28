"""Single-instance lock: refuse to start a second vrec against the same data directory.

Uses an OS-level advisory lock on `paths.lock`, which the operating system releases
automatically if the process dies (crash, kill, power loss). No PID liveness check is
needed, which is just as well: on Windows, `os.kill(pid, 0)` doesn't mean "is it alive"
the way it does on POSIX (signal 0 there is `CTRL_C_EVENT`).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import TracebackType

from vrec.errors import VrecError


class InstanceLock:
    """Context manager holding an exclusive lock on a file for the lifetime of the process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> InstanceLock:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock(fd)
        except OSError as e:
            os.close(fd)
            raise VrecError(
                f"Another vrec window is already running. Close it first (lock file: {path})."
            ) from e
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with contextlib.suppress(OSError):
            _unlock(fd)
        os.close(fd)
        with contextlib.suppress(OSError):  # deleting the file is a nicety, not required
            self.path.unlink()


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
