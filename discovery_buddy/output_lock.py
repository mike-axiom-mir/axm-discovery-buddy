from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
from typing import BinaryIO, Iterator


class OutputWriterBusy(OSError):
    """Another local process currently owns publication for this output pair."""


def lock_path_for(json_path: Path) -> Path:
    return json_path.parent / f".{json_path.stem}.writer.lock"


def _open_lock_file(lock_path: Path) -> BinaryIO:
    if lock_path.is_symlink():
        raise OSError("refusing symlinked discovery writer lock")

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(lock_path, flags, 0o600)
    try:
        stat = os.fstat(fd)
        if not os.path.isfile(lock_path) or not stat.st_mode:
            raise OSError("discovery writer lock is not a regular file")
        # Windows byte-range locking requires at least one byte. Keeping one fixed byte also
        # makes the persistent lock inode intentionally content-free coordination state.
        if stat.st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        return os.fdopen(fd, "r+b", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _acquire_nonblocking(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise OutputWriterBusy("discovery output pair already has an active writer") from exc
            raise
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise OutputWriterBusy("discovery output pair already has an active writer") from exc
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise OutputWriterBusy("discovery output pair already has an active writer") from exc
        raise


@contextmanager
def output_writer_lock(json_path: Path) -> Iterator[None]:
    """Hold single-writer ownership for one local/public discovery output pair.

    The lock is advisory and host-local. The file itself may persist, but ownership belongs to
    the open descriptor and is released by the operating system when that descriptor closes or
    the process exits. It carries no discovery data and grants no authority beyond serializing
    this process-level publication/recovery boundary.
    """

    lock_path = lock_path_for(json_path)
    handle = _open_lock_file(lock_path)
    try:
        _acquire_nonblocking(handle)
        yield
    finally:
        # Closing the descriptor releases flock/msvcrt ownership, including normal exceptions.
        # Abrupt process death also closes descriptors at the OS boundary, avoiding stale-owner
        # metadata that would need to be guessed or manually cleared.
        handle.close()
