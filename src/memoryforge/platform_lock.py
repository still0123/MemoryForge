"""Small cross-platform exclusive file lock."""

from __future__ import annotations

import errno
import math
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

_fcntl: ModuleType | None = None
_msvcrt: ModuleType | None = None
if sys.platform == "win32":
    import msvcrt as _msvcrt
else:
    import fcntl as _fcntl

_POSIX_CONTENTION = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})
_WINDOWS_CONTENTION = frozenset({13, 16, 33, 36})


class UnsafeLockFileError(OSError):
    """The opened lock descriptor does not identify the expected resource."""


def try_lock_descriptor(descriptor: int) -> bool:
    """Try one exclusive acquisition without closing the caller-owned descriptor."""
    if sys.platform == "win32":
        return _try_lock_windows(descriptor)
    return _try_lock_posix(descriptor)


def lock_descriptor(descriptor: int, *, poll_interval: float = 0.05) -> None:
    """Block until the caller-owned descriptor has an exclusive lock."""
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("poll_interval must be finite and greater than zero")
    while not try_lock_descriptor(descriptor):
        time.sleep(poll_interval)


def unlock_descriptor(descriptor: int) -> None:
    """Release the exclusive lock without closing the caller-owned descriptor."""
    if sys.platform == "win32":
        _unlock_windows(descriptor)
    else:
        _unlock_posix(descriptor)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Lock one stable regular file and retain its pathname after release."""
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise UnsafeLockFileError("lock file could not be opened safely") from exc
    locked = False
    try:
        _require_same_regular_file(path, descriptor)
        lock_descriptor(descriptor)
        locked = True
        _require_same_regular_file(path, descriptor)
        yield
    finally:
        try:
            if locked:
                unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def exclusive_workspace_lock(root: Path, lock_path: Path) -> Iterator[None]:
    """Lock one Workspace namespace with the native platform primitive."""
    if sys.platform == "win32":
        with exclusive_file_lock(lock_path):
            yield
        return

    directory_flag = getattr(os, "O_DIRECTORY", None)
    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if directory_flag is None or no_follow_flag is None:
        raise UnsafeLockFileError("Workspace directory locking is unsupported")
    try:
        descriptor = os.open(root, os.O_RDONLY | directory_flag | no_follow_flag)
    except OSError as exc:
        raise UnsafeLockFileError("Workspace directory could not be opened safely") from exc
    locked = False
    try:
        _require_same_directory(root, descriptor)
        lock_descriptor(descriptor)
        locked = True
        _require_same_directory(root, descriptor)
        with exclusive_file_lock(lock_path):
            yield
    finally:
        try:
            if locked:
                unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _try_lock_posix(descriptor: int) -> bool:
    if _fcntl is None:
        raise OSError(errno.ENOSYS, "fcntl is unavailable")
    try:
        _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _POSIX_CONTENTION:
            return False
        raise
    return True


def _unlock_posix(descriptor: int) -> None:
    if _fcntl is None:
        raise OSError(errno.ENOSYS, "fcntl is unavailable")
    _fcntl.flock(descriptor, _fcntl.LOCK_UN)


def _try_lock_windows(descriptor: int) -> bool:
    if _msvcrt is None:
        raise OSError(errno.ENOSYS, "msvcrt is unavailable")
    try:
        _windows_locking(descriptor, _msvcrt.LK_NBLCK)
    except OSError as exc:
        if exc.errno in _WINDOWS_CONTENTION or getattr(exc, "winerror", None) in (
            _WINDOWS_CONTENTION
        ):
            return False
        raise
    return True


def _unlock_windows(descriptor: int) -> None:
    if _msvcrt is None:
        raise OSError(errno.ENOSYS, "msvcrt is unavailable")
    _windows_locking(descriptor, _msvcrt.LK_UNLCK)


def _windows_locking(descriptor: int, mode: int) -> None:
    if _msvcrt is None:
        raise OSError(errno.ENOSYS, "msvcrt is unavailable")
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, mode, 1)
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)


def _require_same_regular_file(path: Path, descriptor: int) -> None:
    try:
        path_stat = path.stat(follow_symlinks=False)
        descriptor_stat = os.fstat(descriptor)
    except OSError as exc:
        raise UnsafeLockFileError("lock file identity could not be verified") from exc
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or not stat.S_ISREG(descriptor_stat.st_mode)
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        raise UnsafeLockFileError("lock path must identify the opened regular file")


def _require_same_directory(path: Path, descriptor: int) -> None:
    try:
        path_stat = path.stat(follow_symlinks=False)
        descriptor_stat = os.fstat(descriptor)
    except OSError as exc:
        raise UnsafeLockFileError("Workspace directory identity could not be verified") from exc
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or not stat.S_ISDIR(descriptor_stat.st_mode)
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        raise UnsafeLockFileError("Workspace path must identify the opened directory")
