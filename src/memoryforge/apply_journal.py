"""Crash recovery for one in-progress Wiki apply."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memoryforge.changesets import ChangeSetStore
from memoryforge.errors import WorkspaceError
from memoryforge.workspace import Workspace, rebuild_applied_projection

_JOURNAL_NAME = "apply-journal.json"


class ApplyJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    changeset_id: str = Field(pattern=r"^chg_[A-Za-z0-9_-]+$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    paths: tuple[str, ...] = Field(min_length=1)
    phase: Literal["prepared", "committed"] = "prepared"
    commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40,64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> ApplyJournal:
        if self.phase == "prepared" and self.commit is not None:
            raise ValueError("prepared apply journal must not contain a Commit")
        if self.phase == "committed" and self.commit is None:
            raise ValueError("committed apply journal requires a Commit")
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("apply journal paths must not contain duplicates")
        for value in self.paths:
            path = PurePosixPath(value)
            if (
                not path.parts
                or path.parts[0] != "wiki"
                or any(part in {"", ".", ".."} for part in path.parts)
                or "\\" in value
                or path.as_posix() != value
            ):
                raise ValueError("apply journal paths must stay below wiki/")
        return self


class ApplyJournalStore:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.directory = workspace.staging_dir
        self.path = self.directory / _JOURNAL_NAME

    def load(self) -> ApplyJournal | None:
        self.workspace.validate_internal_directory(self.directory)
        directory_fd = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            try:
                descriptor = os.open(
                    _JOURNAL_NAME,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return None
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise WorkspaceError("apply journal must be a regular file")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    payload = source.read(64 * 1024 + 1)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)
        if len(payload) > 64 * 1024:
            raise WorkspaceError("apply journal exceeds its size limit")
        try:
            return ApplyJournal.model_validate_json(payload)
        except ValidationError as exc:
            raise WorkspaceError("apply journal is invalid") from exc

    def prepare(self, changeset_id: str, base_commit: str, paths: tuple[str, ...]) -> ApplyJournal:
        if self.load() is not None:
            raise WorkspaceError("another interrupted apply requires recovery")
        journal = ApplyJournal(
            changeset_id=changeset_id,
            base_commit=base_commit,
            paths=tuple(sorted(paths)),
        )
        self._write(journal)
        return journal

    def mark_committed(self, journal: ApplyJournal, commit: str) -> ApplyJournal:
        current = self.load()
        if current != journal:
            raise WorkspaceError("apply journal changed before Commit recording")
        committed = journal.model_copy(update={"phase": "committed", "commit": commit})
        self._write(committed)
        return committed

    def clear(self) -> None:
        self.workspace.validate_internal_directory(self.directory)
        directory_fd = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            try:
                os.unlink(_JOURNAL_NAME, dir_fd=directory_fd)
            except FileNotFoundError:
                return
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _write(self, journal: ApplyJournal) -> None:
        self.workspace.validate_internal_directory(self.directory)
        payload = (journal.model_dump_json(indent=2) + "\n").encode()
        temporary = f".{_JOURNAL_NAME}.{uuid.uuid4().hex}.tmp"
        directory_fd = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "wb", closefd=False) as destination:
                destination.write(payload)
                destination.flush()
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary,
                _JOURNAL_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except OSError as exc:
            raise WorkspaceError("apply journal could not be written safely") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)
            os.close(directory_fd)


def recover_interrupted_apply(workspace: Workspace) -> None:
    store = ApplyJournalStore(workspace)
    try:
        os.lstat(store.path)
    except FileNotFoundError:
        return
    with workspace.exclusive_lock():
        journal = store.load()
        if journal is not None:
            _recover_locked(workspace, store, journal)


def _recover_locked(
    workspace: Workspace,
    store: ApplyJournalStore,
    journal: ApplyJournal,
) -> None:
    head = workspace.current_commit()
    commit: str | None = None
    if head == journal.base_commit:
        if journal.phase == "committed":
            raise WorkspaceError("apply journal Commit is missing from Workspace history")
        workspace.version_store.restore_paths(journal.base_commit, journal.paths)
        rebuild_applied_projection(workspace)
        store.clear()
        return

    if (
        journal.phase == "committed"
        and journal.commit == head
        or journal.phase == "prepared"
        and workspace.version_store.commit_subject(head)
        == f"knowledge: apply {journal.changeset_id}"
        and workspace.version_store.is_ancestor(journal.base_commit, head)
    ):
        commit = head
    if commit is None:
        raise WorkspaceError("apply journal does not match the current Workspace Commit")

    workspace.version_store.restore_paths(commit, journal.paths)
    rebuild_applied_projection(workspace)
    changesets = ChangeSetStore(workspace)
    pending = workspace.staging_dir / journal.changeset_id
    applied = workspace.staging_dir / "applied" / journal.changeset_id
    if pending.exists():
        stored = changesets.get_for_recovery(journal.changeset_id)
        if stored.changeset.base_commit != journal.base_commit:
            raise WorkspaceError("apply journal does not match its staged ChangeSet")
        for path, content in stored.candidate_files.items():
            if workspace.version_store.read_text_at(commit, path) != content:
                raise WorkspaceError("apply Commit does not match its staged candidate")
        changesets.archive_applied(stored, commit=commit)
    elif applied.exists():
        changesets.ensure_applied_receipt(journal.changeset_id, commit=commit)
    else:
        raise WorkspaceError("apply journal has no staged or archived ChangeSet")
    store.clear()
