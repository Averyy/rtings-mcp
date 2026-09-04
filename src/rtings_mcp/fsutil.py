"""Atomic writes and a cross-process advisory lock (SPEC §8).

Two rules this module exists to enforce:

* the temp file lives **in the target directory**, never ``/tmp`` — ``os.replace`` across
  filesystems is a copy, not an atomic rename;
* ``fcntl.flock`` blocks the event loop, so every lock acquisition runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False

try:  # Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ImportError:
    msvcrt = None  # type: ignore[assignment]
    _HAVE_MSVCRT = False

TMP_PREFIX = ".tmp-"
DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_dir(path: Path) -> None:
    """Create ``path`` (and parents) owner-only. Member payloads are data the user paid for."""
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)


def atomic_write_bytes(path: Path, data: bytes, *, mtime: float | None = None) -> None:
    """Write ``data`` to ``path`` atomically: temp-in-target-dir, fsync, ``os.replace``.

    ``mtime`` is set to the payload's ``fetched_at`` so TTL and staleness are a ``stat``,
    not a parse of a 442 KB body. That also makes mtime useless as a recency signal, which
    is why the LRU index is explicit (SPEC §8).
    """
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=TMP_PREFIX)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, FILE_MODE)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def sweep_temp_files(root: Path) -> int:
    """Remove orphaned ``.tmp-*`` files left by a crashed writer. Returns the count."""
    removed = 0
    if not root.exists():
        return 0
    for path in root.rglob(f"{TMP_PREFIX}*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


def _lock_fd(fd: int) -> None:
    if _HAVE_FCNTL:
        fcntl.flock(fd, fcntl.LOCK_EX)
    elif _HAVE_MSVCRT:  # pragma: no cover - Windows
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)


def _unlock_fd(fd: int) -> None:
    if _HAVE_FCNTL:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif _HAVE_MSVCRT:  # pragma: no cover - Windows
        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def file_lock_sync(path: Path) -> Iterator[None]:
    """Blocking advisory lock on ``path``. Callers on the event loop must use the async form."""
    ensure_dir(path.parent)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        _lock_fd(fd)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


@contextlib.asynccontextmanager
async def file_lock(path: Path) -> AsyncIterator[None]:
    """Cross-process advisory lock, acquired off the event loop.

    Used around miss -> fetch -> write so two MCP processes sharing a cache dir cannot
    double-spend a metered preview. **Never hold this while waiting on any other lock**
    (SPEC §8) — the per-host cooldown is deliberately lock-free for exactly that reason.
    """
    ensure_dir(path.parent)
    fd = await asyncio.to_thread(os.open, str(path), os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        await asyncio.to_thread(_lock_fd, fd)
        try:
            yield
        finally:
            await asyncio.to_thread(_unlock_fd, fd)
    finally:
        await asyncio.to_thread(os.close, fd)
