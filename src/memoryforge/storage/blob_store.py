"""Secure content-addressed blob storage."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from memoryforge.storage.database import connect
from memoryforge.storage.errors import WorkspaceIntegrityError, WorkspaceSecurityError

_BLOB_ROOT = Path("raw/blobs")
_SECURE_DIR_FD_SUPPORTED = all(
    function in os.supports_dir_fd for function in (os.open, os.mkdir, os.stat, os.unlink)
)


def write_blob(root: Path, content_sha256: str, content: bytes) -> tuple[Path, bool]:
    relative = blob_relative_path(content_sha256)
    filename = relative.name
    with open_blob_chain(root, content_sha256, create=True) as chain:
        prefix_fd = chain[-1][2]
        _cleanup_stale_blob_temps(prefix_fd, content_sha256)
        temp_name = f"{content_sha256}.tmp-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow_flag()
        try:
            descriptor = os.open(temp_name, flags, 0o600, dir_fd=prefix_fd)
            with os.fdopen(descriptor, "wb") as snapshot:
                snapshot.write(content)
                snapshot.flush()
                os.fsync(snapshot.fileno())
                os.fchmod(snapshot.fileno(), 0o600)
            _assert_blob_chain(root, chain)
            try:
                os.link(
                    temp_name,
                    filename,
                    src_dir_fd=prefix_fd,
                    dst_dir_fd=prefix_fd,
                    follow_symlinks=False,
                )
                os.fsync(prefix_fd)
                created = True
            except FileExistsError:
                try:
                    verify_blob_hash(root, content_sha256, relative)
                    created = False
                except WorkspaceIntegrityError:
                    os.unlink(filename, dir_fd=prefix_fd)
                    os.fsync(prefix_fd)
                    os.link(
                        temp_name,
                        filename,
                        src_dir_fd=prefix_fd,
                        dst_dir_fd=prefix_fd,
                        follow_symlinks=False,
                    )
                    os.fsync(prefix_fd)
                    created = True
            _assert_blob_chain(root, chain)
            return relative, created
        finally:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=prefix_fd)
                os.fsync(prefix_fd)


def _cleanup_stale_blob_temps(prefix_fd: int, content_sha256: str) -> None:
    prefix = f"{content_sha256}.tmp-"
    for name in os.listdir(prefix_fd):
        if name.startswith(prefix):
            with suppress(OSError):
                os.unlink(name, dir_fd=prefix_fd)
    os.fsync(prefix_fd)


def cleanup_blob_temps(root: Path, content_sha256: str) -> None:
    try:
        with open_blob_chain(root, content_sha256, create=False) as chain:
            _cleanup_stale_blob_temps(chain[-1][2], content_sha256)
    except FileNotFoundError:
        return


def verify_blob_hash(
    root: Path,
    content_sha256: str,
    relative: Path,
    *,
    repair_permissions: bool = True,
) -> None:
    read_blob_bytes(
        root,
        content_sha256,
        relative,
        repair_permissions=repair_permissions,
    )


def read_blob_bytes(
    root: Path,
    content_sha256: str,
    relative: Path,
    *,
    repair_permissions: bool = True,
) -> bytes:
    expected_relative = blob_relative_path(content_sha256)
    if relative != expected_relative:
        raise WorkspaceIntegrityError("blob integrity metadata is inconsistent")

    content = bytearray()
    try:
        with open_blob_chain(root, content_sha256, create=False) as chain:
            prefix_fd = chain[-1][2]
            descriptor = os.open(
                expected_relative.name,
                os.O_RDONLY | no_follow_flag(),
                dir_fd=prefix_fd,
            )
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise WorkspaceSecurityError("immutable blob must be a regular file")
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "rb", closefd=False) as snapshot:
                    for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
                        digest.update(chunk)
                        content.extend(chunk)
                if repair_permissions:
                    os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            _assert_blob_chain(root, chain)
    except FileNotFoundError as exc:
        raise WorkspaceIntegrityError("blob integrity check failed: evidence is missing") from exc
    except OSError as exc:
        raise WorkspaceSecurityError("secure blob verification failed") from exc

    if digest.hexdigest() != content_sha256:
        raise WorkspaceIntegrityError("blob integrity check failed: digest mismatch")
    return bytes(content)


def cleanup_orphan_blob(root: Path, database_path: Path, content_sha256: str) -> None:
    try:
        with connect(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM blobs WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            if exists is None:
                unlink_blob(root, content_sha256)
    except (OSError, sqlite3.Error):
        return


def unlink_blob(root: Path, content_sha256: str) -> None:
    try:
        with open_blob_chain(root, content_sha256, create=False) as chain:
            os.unlink(
                blob_relative_path(content_sha256).name,
                dir_fd=chain[-1][2],
            )
    except (FileNotFoundError, WorkspaceSecurityError):
        return


@contextmanager
def open_blob_chain(
    root: Path,
    content_sha256: str,
    *,
    create: bool,
) -> Iterator[list[tuple[int, str, int]]]:
    _require_secure_dir_fd_support()
    descriptors: list[int] = []
    chain: list[tuple[int, str, int]] = []
    directory_flags = os.O_RDONLY | _directory_flag() | no_follow_flag()
    try:
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        raw_fd = _open_directory_at(root_fd, "raw", create=False)
        descriptors.append(raw_fd)
        chain.append((root_fd, "raw", raw_fd))
        blobs_fd = _open_directory_at(raw_fd, "blobs", create=create)
        descriptors.append(blobs_fd)
        chain.append((raw_fd, "blobs", blobs_fd))
        prefix = content_sha256[:2]
        prefix_fd = _open_directory_at(blobs_fd, prefix, create=create)
        descriptors.append(prefix_fd)
        chain.append((blobs_fd, prefix, prefix_fd))
        _assert_blob_chain(root, chain)
        yield chain
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | _directory_flag() | no_follow_flag()
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        with suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    os.fchmod(descriptor, 0o700)
    return descriptor


def _assert_blob_chain(root: Path, chain: list[tuple[int, str, int]]) -> None:
    root_stat = root.stat(follow_symlinks=False)
    opened_root_stat = os.fstat(chain[0][0])
    if (root_stat.st_dev, root_stat.st_ino) != (
        opened_root_stat.st_dev,
        opened_root_stat.st_ino,
    ):
        raise WorkspaceSecurityError("workspace changed during blob operation")

    for parent_fd, name, child_fd in chain:
        path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened_stat = os.fstat(child_fd)
        if not stat.S_ISDIR(path_stat.st_mode) or (path_stat.st_dev, path_stat.st_ino) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ):
            raise WorkspaceSecurityError("workspace directory changed during blob operation")


def _require_secure_dir_fd_support() -> None:
    if not _SECURE_DIR_FD_SUPPORTED:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")


def _directory_flag() -> int:
    flag = getattr(os, "O_DIRECTORY", None)
    if flag is None:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    return int(flag)


def no_follow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    return int(flag)


def blob_relative_path(content_sha256: str) -> Path:
    return _BLOB_ROOT / content_sha256[:2] / f"{content_sha256}.blob"


def blob_uri(content_sha256: str) -> str:
    return f"mf://blob/{content_sha256}"
