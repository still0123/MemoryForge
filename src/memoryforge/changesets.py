"""Durable, immutable staging storage for proposed Wiki modifications."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from memoryforge.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.models import ChangeSet, ChangeSetStatus, StagedChangeSet, StagedWikiFile
from memoryforge.workspace import Workspace, WorkspaceIntegrityError

CHANGESET_ID_PATTERN = re.compile(r"^chg_[A-Za-z0-9_-]+$")
CHANGESET_FILENAME = "changeset.json"
CHANGESET_DIGEST_FILENAME = "changeset.sha256"
PROPOSED_DIRECTORY = "proposed"


@dataclass(frozen=True)
class StoredChangeSet:
    record: StagedChangeSet
    directory: Path
    candidate_files: dict[str, str]

    @property
    def changeset(self) -> ChangeSet:
        return self.record.changeset


class ChangeSetStore:
    """Stores PROPOSED records without writing the stable Wiki tree."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.staging_dir = workspace.staging_dir

    def create(
        self,
        changeset: ChangeSet,
        candidate_files: Mapping[str, str],
    ) -> StoredChangeSet:
        if changeset.status is not ChangeSetStatus.PROPOSED:
            raise ChangeSetStoreError("New ChangeSets must start in PROPOSED state")
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        temp_name = f".{changeset.changeset_id}.{uuid.uuid4().hex}.tmp"
        try:
            fcntl.flock(staging_fd, fcntl.LOCK_EX)
            current_commit = self.workspace.current_commit()
            if changeset.base_commit != current_commit:
                raise ChangeSetStoreError(
                    "ChangeSet base_commit does not match the current workspace revision: "
                    f"expected {current_commit}, received {changeset.base_commit}"
                )
            if _entry_exists(staging_fd, changeset.changeset_id):
                return self._require_idempotent(changeset.changeset_id, changeset, candidate_files)
            try:
                self.workspace.validate_changeset_evidence(changeset)
            except (WorkspaceError, WorkspaceIntegrityError, ValueError) as exc:
                raise ChangeSetStoreError(
                    f"ChangeSet source or citation evidence is invalid: {exc}"
                ) from exc

            record = self._build_record(changeset, candidate_files)
            os.mkdir(temp_name, 0o700, dir_fd=staging_fd)
            temp_fd = os.open(
                temp_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            try:
                self._write_record(temp_fd, record, candidate_files)
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)
            try:
                current_commit = self.workspace.current_commit()
                if changeset.base_commit != current_commit:
                    raise ChangeSetStoreError(
                        "ChangeSet base_commit changed before publish: "
                        f"expected {current_commit}, received {changeset.base_commit}"
                    )
                os.rename(
                    temp_name,
                    changeset.changeset_id,
                    src_dir_fd=staging_fd,
                    dst_dir_fd=staging_fd,
                )
                os.fsync(staging_fd)
            except FileExistsError:
                return self._require_idempotent(changeset.changeset_id, changeset, candidate_files)
        except OSError as exc:
            raise ChangeSetStoreError("ChangeSet could not be published safely") from exc
        finally:
            os.close(staging_fd)
            temporary_path = self.staging_dir / temp_name
            if temporary_path.exists() and not temporary_path.is_symlink():
                shutil.rmtree(temporary_path)
        return self.get(changeset.changeset_id)

    def get(self, changeset_id: str) -> StoredChangeSet:
        _validate_changeset_id(changeset_id)
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        try:
            try:
                directory_fd = os.open(
                    changeset_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=staging_fd,
                )
            except OSError as exc:
                raise ChangeSetStoreError(
                    f"Staged ChangeSet does not exist safely: {changeset_id}"
                ) from exc
            try:
                metadata = _read_regular_file(directory_fd, CHANGESET_FILENAME)
                recorded_digest = _read_regular_file(
                    directory_fd, CHANGESET_DIGEST_FILENAME
                ).decode("ascii")
                if recorded_digest != hashlib.sha256(metadata).hexdigest() + "\n":
                    raise ChangeSetStoreError(
                        f"Staged ChangeSet metadata integrity check failed: {changeset_id}"
                    )
                try:
                    record = StagedChangeSet.model_validate_json(metadata)
                except ValidationError as exc:
                    raise ChangeSetStoreError(
                        f"Staged ChangeSet metadata is invalid: {changeset_id}"
                    ) from exc
                if (
                    record.changeset.changeset_id != changeset_id
                    or record.changeset.status is not ChangeSetStatus.PROPOSED
                ):
                    raise ChangeSetStoreError(
                        "Staged ChangeSet immutable identity or state was modified"
                    )
                candidate_files = self._read_candidates(directory_fd, record)
                self._require_current_base(record.changeset)
            finally:
                os.close(directory_fd)
        finally:
            os.close(staging_fd)
        return StoredChangeSet(
            record,
            self.staging_dir / changeset_id,
            candidate_files,
        )

    def list_all(self) -> list[StoredChangeSet]:
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        try:
            identifiers = sorted(
                name for name in os.listdir(staging_fd) if CHANGESET_ID_PATTERN.fullmatch(name)
            )
        finally:
            os.close(staging_fd)
        return [self.get(identifier) for identifier in identifiers]

    def archive_applied(self, stored: StoredChangeSet, *, commit: str) -> Path:
        """Move an applied proposal out of the pending staging namespace."""
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        try:
            fcntl.flock(staging_fd, fcntl.LOCK_EX)
            with suppress(FileExistsError):
                os.mkdir("applied", 0o700, dir_fd=staging_fd)
            applied_fd = os.open(
                "applied",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            try:
                os.rename(
                    stored.changeset.changeset_id,
                    stored.changeset.changeset_id,
                    src_dir_fd=staging_fd,
                    dst_dir_fd=applied_fd,
                )
                archived_fd = os.open(
                    stored.changeset.changeset_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=applied_fd,
                )
                try:
                    receipt = {
                        "status": "APPLIED",
                        "commit": commit,
                        "applied_at": datetime.now(UTC).isoformat(),
                        "changeset_id": stored.changeset.changeset_id,
                    }
                    receipt_payload = (
                        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    ).encode()
                    _write_new_file(archived_fd, "receipt.json", receipt_payload)
                    if json.loads(_read_regular_file(archived_fd, "receipt.json")) != receipt:
                        raise ChangeSetStoreError("Applied ChangeSet receipt is invalid")
                finally:
                    os.close(archived_fd)
                os.fsync(applied_fd)
                os.fsync(staging_fd)
            finally:
                os.close(applied_fd)
        except OSError as exc:
            raise ChangeSetStoreError("Applied ChangeSet could not be archived") from exc
        finally:
            os.close(staging_fd)
        return self.staging_dir / "applied" / stored.changeset.changeset_id

    def _require_current_base(self, changeset: ChangeSet) -> None:
        current_commit = self.workspace.current_commit()
        if changeset.base_commit != current_commit:
            raise ChangeSetStoreError(
                "Staged ChangeSet base_commit does not match the current workspace revision: "
                f"expected {current_commit}, received {changeset.base_commit}"
            )

    def _read_candidates(
        self,
        directory_fd: int,
        record: StagedChangeSet,
    ) -> dict[str, str]:
        candidate_files: dict[str, str] = {}
        proposed_fd = _open_directory_chain(directory_fd, (PROPOSED_DIRECTORY,))
        try:
            for proposed in record.proposed_files:
                parts = _candidate_parts(proposed.path)
                parent_fd = _open_directory_chain(proposed_fd, parts[:-1])
                try:
                    content_bytes = _read_regular_file(parent_fd, parts[-1])
                finally:
                    os.close(parent_fd)
                try:
                    content = content_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ChangeSetStoreError(
                        f"Staged candidate is not UTF-8: {proposed.path}"
                    ) from exc
                if hashlib.sha256(content_bytes).hexdigest() != proposed.content_sha256:
                    raise ChangeSetStoreError(
                        f"Staged candidate hash does not match metadata: {proposed.path}"
                    )
                if len(content_bytes) != proposed.byte_count:
                    raise ChangeSetStoreError(
                        f"Staged candidate byte count does not match metadata: {proposed.path}"
                    )
                candidate_files[proposed.path] = content
        finally:
            os.close(proposed_fd)
        return candidate_files

    def _require_idempotent(
        self,
        changeset_id: str,
        changeset: ChangeSet,
        candidate_files: Mapping[str, str],
    ) -> StoredChangeSet:
        existing = self.get(changeset_id)
        if existing.changeset == changeset and existing.candidate_files == dict(candidate_files):
            return existing
        raise ChangeSetStoreError(f"Refusing to replace staged ChangeSet: {changeset_id}")

    def _build_record(
        self,
        changeset: ChangeSet,
        candidate_files: Mapping[str, str],
    ) -> StagedChangeSet:
        proposed_files = tuple(
            StagedWikiFile(
                path=path,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                byte_count=len(content.encode()),
            )
            for path, content in sorted(candidate_files.items())
        )
        try:
            return StagedChangeSet(
                staged_at=datetime.now(UTC),
                changeset=changeset,
                proposed_files=proposed_files,
            )
        except ValidationError as exc:
            raise ChangeSetStoreError(f"Invalid ChangeSet staging payload: {exc}") from exc

    def _write_record(
        self,
        directory_fd: int,
        record: StagedChangeSet,
        candidate_files: Mapping[str, str],
    ) -> None:
        proposed_fd = _create_directory_chain(directory_fd, (PROPOSED_DIRECTORY,))
        try:
            for path, content in candidate_files.items():
                parts = _candidate_parts(path)
                parent_fd = _create_directory_chain(proposed_fd, parts[:-1])
                try:
                    _write_new_file(parent_fd, parts[-1], content.encode())
                finally:
                    os.close(parent_fd)
            os.fsync(proposed_fd)
        finally:
            os.close(proposed_fd)
        metadata = (record.model_dump_json(indent=2) + "\n").encode()
        _write_new_file(directory_fd, CHANGESET_FILENAME, metadata)
        _write_new_file(
            directory_fd,
            CHANGESET_DIGEST_FILENAME,
            (hashlib.sha256(metadata).hexdigest() + "\n").encode("ascii"),
        )
        os.fsync(directory_fd)

    def _open_staging(self) -> int:
        try:
            return os.open(
                self.staging_dir,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise ChangeSetStoreError("ChangeSet staging directory is unsafe") from exc


def _candidate_parts(wiki_path: str) -> tuple[str, ...]:
    relative_path = PurePosixPath(wiki_path)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or relative_path.parts[0] != "wiki"
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or "\\" in wiki_path
    ):
        raise ChangeSetStoreError(f"Invalid staged Wiki path: {wiki_path}")
    return relative_path.parts


def _open_directory_chain(root_fd: int, parts: Sequence[str]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        os.close(current_fd)
        raise ChangeSetStoreError("Staged candidate directory chain is unsafe") from exc


def _create_directory_chain(root_fd: int, parts: Sequence[str]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
                os.fsync(current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.fchmod(next_fd, 0o700)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        os.close(current_fd)
        raise ChangeSetStoreError("Candidate directory could not be created safely") from exc


def _read_regular_file(directory_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise ChangeSetStoreError(f"Staged file could not be opened safely: {name}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ChangeSetStoreError(f"Staged file must be regular: {name}")
        with os.fdopen(descriptor, "rb", closefd=False) as staged_file:
            return staged_file.read()
    finally:
        os.close(descriptor)


def _write_new_file(directory_fd: int, name: str, payload: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)


def _entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _validate_changeset_id(changeset_id: str) -> None:
    if not CHANGESET_ID_PATTERN.fullmatch(changeset_id):
        raise ChangeSetStoreError(f"Invalid ChangeSet ID: {changeset_id}")
