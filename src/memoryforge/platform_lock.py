"""Small cross-platform exclusive file lock."""

from __future__ import annotations

import errno
import hashlib
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
_pwd: ModuleType | None = None
if sys.platform == "win32":
    import msvcrt as _msvcrt
else:
    import fcntl as _fcntl
    import pwd as _pwd

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

    try:
        relative_lock = lock_path.relative_to(root)
        canonical_root = root.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise UnsafeLockFileError("Workspace namespace could not be resolved safely") from exc
    with (
        exclusive_posix_directory_lock(canonical_root),
        exclusive_file_lock(canonical_root / relative_lock),
    ):
        yield


@contextmanager
def exclusive_posix_directory_lock(path: Path) -> Iterator[int]:
    """Lock one POSIX directory path and its current inode."""
    if sys.platform == "win32":
        raise UnsafeLockFileError("POSIX directory locking is unavailable on Windows")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise UnsafeLockFileError("directory namespace could not be resolved safely") from exc
    namespace_lock = _posix_namespace_lock_path(canonical)
    with (
        exclusive_file_lock(namespace_lock),
        _exclusive_posix_directory_lock(canonical) as descriptor,
    ):
        yield descriptor


def _posix_namespace_lock_path(path: Path) -> Path:
    get_effective_user = getattr(os, "geteuid", None)
    if get_effective_user is None or _pwd is None:
        raise UnsafeLockFileError("effective user identity is unavailable")
    user_id = get_effective_user()
    try:
        home = Path(_pwd.getpwuid(user_id).pw_dir).resolve(strict=True)
        home_metadata = home.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(home_metadata.st_mode)
            or home_metadata.st_uid != user_id
            or stat.S_IMODE(home_metadata.st_mode) & 0o022
        ):
            raise UnsafeLockFileError("user home must be owner-controlled")
        lock_root = home / ".memoryforge-locks"
        lock_root.mkdir(mode=0o700, exist_ok=True)
        metadata = lock_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise UnsafeLockFileError("namespace lock directory is unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != user_id
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise UnsafeLockFileError("namespace lock directory must be private and owner-controlled")
    digest = hashlib.sha256(os.fsencode(str(path))).hexdigest()
    return lock_root / f"path-{digest}.lock"


@contextmanager
def _exclusive_posix_directory_lock(path: Path) -> Iterator[int]:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if directory_flag is None or no_follow_flag is None:
        raise UnsafeLockFileError("Workspace directory locking is unsupported")
    try:
        descriptor = os.open(path, os.O_RDONLY | directory_flag | no_follow_flag)
    except OSError as exc:
        raise UnsafeLockFileError("Workspace directory could not be opened safely") from exc
    locked = False
    try:
        _require_same_directory(path, descriptor)
        lock_descriptor(descriptor)
        locked = True
        _require_same_directory(path, descriptor)
        yield descriptor
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
