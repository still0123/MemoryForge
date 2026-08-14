"""Append-only manifests for immutable SourceVersions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from memoryforge.core.errors import WorkspaceError
from memoryforge.core.models import SourceVersionManifest

_MANIFEST_NAME = re.compile(r"^(?P<source_id>[a-f0-9]{64})--(?P<record_sha256>[a-f0-9]{64})\.json$")
_MANIFEST_TEMP_NAME = re.compile(r"^\.[a-f0-9]{64}--[a-f0-9]{64}\.json\.[a-f0-9]{32}\.tmp$")
_LEGACY_MANIFEST_NAME = re.compile(r"^(?P<source_id>src_[a-f0-9]{16})\.json$")


class SourceManifestStore:
    """Read and publish manifests through a no-follow directory descriptor."""

    def __init__(self, manifest_dir: Path) -> None:
        self.manifest_dir = manifest_dir

    def list_all(self) -> list[SourceVersionManifest]:
        try:
            directory_fd = self._open_directory(create=False)
        except FileNotFoundError:
            return []
        try:
            manifests: list[SourceVersionManifest] = []
            for name in sorted(os.listdir(directory_fd)):
                match = _MANIFEST_NAME.fullmatch(name)
                if match is None:
                    legacy_match = _LEGACY_MANIFEST_NAME.fullmatch(name)
                    if legacy_match is not None:
                        _validate_legacy_manifest(
                            directory_fd, name, legacy_match.group("source_id")
                        )
                        continue
                    if _MANIFEST_TEMP_NAME.fullmatch(name):
                        _remove_owned_temp(directory_fd, name)
                        continue
                    raise WorkspaceError(f"Unexpected source manifest entry: {name}")
                try:
                    payload = _read_regular_file(directory_fd, name)
                except OSError as exc:
                    raise WorkspaceError(
                        f"Source manifest could not be opened safely: {name}"
                    ) from exc
                record_sha256 = hashlib.sha256(payload).hexdigest()
                if record_sha256 != match.group("record_sha256"):
                    raise WorkspaceError(f"Source manifest integrity check failed: {name}")
                try:
                    manifest = SourceVersionManifest.model_validate_json(payload)
                except ValidationError as exc:
                    raise WorkspaceError(f"Source manifest is invalid: {name}") from exc
                if manifest.source_id != match.group("source_id"):
                    raise WorkspaceError(f"Source manifest identity check failed: {name}")
                self._validate_local_evidence(manifest)
                manifests.append(manifest)
            return manifests
        finally:
            os.close(directory_fd)

    def write(self, manifest: SourceVersionManifest) -> Path:
        payload, name = _manifest_record(manifest)
        directory_fd = self._open_directory(create=True)
        temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            try:
                existing = _read_regular_file(directory_fd, name)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if existing != payload:
                    raise WorkspaceError(f"Refusing to replace immutable manifest: {name}")
                return self.manifest_dir / name

            descriptor = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(descriptor)
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            try:
                os.link(
                    temp_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                os.fsync(directory_fd)
            except FileExistsError:
                if _read_regular_file(directory_fd, name) != payload:
                    raise WorkspaceError(
                        f"Refusing to replace immutable manifest: {name}"
                    ) from None
            return self.manifest_dir / name
        except OSError as exc:
            raise WorkspaceError("Source manifest could not be published safely") from exc
        finally:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            os.close(directory_fd)

    def contains(self, manifest: SourceVersionManifest) -> bool:
        """Check for one exact immutable record without auditing historical Blobs."""
        payload, name = _manifest_record(manifest)
        try:
            directory_fd = self._open_directory(create=False)
        except FileNotFoundError:
            return False
        try:
            try:
                existing = _read_regular_file(directory_fd, name)
            except FileNotFoundError:
                return False
            if existing != payload:
                raise WorkspaceError(f"Immutable source manifest is inconsistent: {name}")
            return True
        except OSError as exc:
            raise WorkspaceError(f"Source manifest could not be opened safely: {name}") from exc
        finally:
            os.close(directory_fd)

    def _open_directory(self, *, create: bool) -> int:
        parent = self.manifest_dir.parent
        try:
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise WorkspaceError("Source manifest parent directory is unsafe") from exc
        try:
            if create:
                try:
                    os.mkdir(self.manifest_dir.name, 0o700, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except FileExistsError:
                    pass
            descriptor = os.open(
                self.manifest_dir.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.fchmod(descriptor, 0o700)
            return descriptor
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise WorkspaceError("Source manifest directory is unsafe") from exc
        finally:
            os.close(parent_fd)

    def _validate_local_evidence(self, manifest: SourceVersionManifest) -> None:
        root = self.manifest_dir.parents[2]
        database_path = root / ".memoryforge/index.sqlite"
        try:
            database_stat = os.lstat(database_path)
        except FileNotFoundError:
            raise WorkspaceError("Source manifest database evidence is missing") from None
        if stat.S_ISLNK(database_stat.st_mode) or not stat.S_ISREG(database_stat.st_mode):
            raise WorkspaceError("Source manifest database evidence is unsafe")
        try:
            with closing(sqlite3.connect(database_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT
                        s.source_uri,
                        s.source_path,
                        v.media_type,
                        v.category,
                        v.title,
                        v.observed_at,
                        v.sensitivity,
                        v.tags_json,
                        v.legacy_category,
                        b.snapshot_path,
                        s.legacy_source_id
                    FROM sources AS s
                    JOIN source_versions AS v ON v.source_id = s.id
                    JOIN blobs AS b ON b.id = v.blob_id
                    WHERE s.source_id = ? AND b.content_sha256 = ?
                    """,
                    (manifest.source_id, manifest.content_sha256),
                ).fetchall()
        except sqlite3.Error as exc:
            raise WorkspaceError("Source manifest database evidence is invalid") from exc
        if not any(_row_matches_manifest(row, manifest) for row in rows):
            raise WorkspaceError(
                "Source manifest does not identify an imported SourceVersion and Blob"
            )
        expected_path = (
            Path("raw/blobs") / manifest.content_sha256[:2] / f"{manifest.content_sha256}.blob"
        )
        if manifest.snapshot_path != expected_path.as_posix():
            raise WorkspaceError("Source manifest Blob path is not canonical")
        payload = _read_blob_secure(root, expected_path)
        if hashlib.sha256(payload).hexdigest() != manifest.content_sha256:
            raise WorkspaceError("Source manifest Blob integrity check failed")


def _read_regular_file(directory_fd: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WorkspaceError(f"Source manifest must be a regular file: {name}")
        with os.fdopen(descriptor, "rb", closefd=False) as manifest_file:
            return manifest_file.read()
    finally:
        os.close(descriptor)


def _manifest_record(manifest: SourceVersionManifest) -> tuple[bytes, str]:
    excluded = {"legacy_source_id"} if manifest.legacy_source_id is None else None
    payload = (manifest.model_dump_json(indent=2, exclude=excluded) + "\n").encode()
    record_sha256 = hashlib.sha256(payload).hexdigest()
    return payload, f"{manifest.source_id}--{record_sha256}.json"


def _row_matches_manifest(
    row: tuple[object, ...],
    manifest: SourceVersionManifest,
) -> bool:
    try:
        tags = json.loads(str(row[7]))
        observed_at = datetime.fromisoformat(str(row[5]))
    except (json.JSONDecodeError, ValueError):
        return False
    return (
        str(row[0]) == manifest.source_uri
        and str(row[1]) == manifest.source_path
        and str(row[2]) == manifest.media_type
        and str(row[3]) == manifest.category.value
        and str(row[4]) == manifest.title
        and observed_at == manifest.observed_at
        and str(row[6]) == manifest.sensitivity.value
        and tags == list(manifest.tags)
        and (str(row[8]) if row[8] is not None else None) == manifest.legacy_category
        and str(row[9]) == manifest.snapshot_path
        and (str(row[10]) if row[10] is not None else None) == manifest.legacy_source_id
    )


def _validate_legacy_manifest(
    directory_fd: int,
    name: str,
    expected_source_id: str,
) -> None:
    try:
        payload = _read_regular_file(directory_fd, name)
        record = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"Legacy source manifest is invalid: {name}") from exc
    if not isinstance(record, dict) or record.get("source_id") != expected_source_id:
        raise WorkspaceError(f"Legacy source manifest identity check failed: {name}")


def _remove_owned_temp(directory_fd: int, name: str) -> None:
    try:
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceError(f"Source manifest temporary entry is unsafe: {name}") from exc
    if not stat.S_ISREG(entry.st_mode):
        raise WorkspaceError(f"Source manifest temporary entry is unsafe: {name}")
    try:
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError as exc:
        raise WorkspaceError(
            f"Source manifest temporary entry could not be cleaned: {name}"
        ) from exc


def _read_blob_secure(root: Path, relative: Path) -> bytes:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.parts
        for part in parts[:-1]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        blob_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=descriptor,
        )
        try:
            if not stat.S_ISREG(os.fstat(blob_descriptor).st_mode):
                raise WorkspaceError("Source manifest Blob must be a regular file")
            with os.fdopen(blob_descriptor, "rb", closefd=False) as blob:
                return blob.read()
        finally:
            os.close(blob_descriptor)
    except OSError as exc:
        raise WorkspaceError("Source manifest Blob could not be opened safely") from exc
    finally:
        os.close(descriptor)
