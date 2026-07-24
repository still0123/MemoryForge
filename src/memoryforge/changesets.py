"""Durable, immutable staging storage for proposed Wiki modifications."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping

from pydantic import ValidationError

from memoryforge.errors import ChangeSetStoreError
from memoryforge.models import ChangeSet, StagedChangeSet, StagedWikiFile
from memoryforge.workspace import Workspace

CHANGESET_ID_PATTERN = re.compile(r"^chg_[A-Za-z0-9_-]+$")
CHANGESET_FILENAME = "changeset.json"
PROPOSED_DIRECTORY = "proposed"


@dataclass(frozen=True)
class StoredChangeSet:
    """A verified ChangeSet record and the candidate Wiki text it references."""

    record: StagedChangeSet
    directory: Path
    candidate_files: dict[str, str]

    @property
    def changeset(self) -> ChangeSet:
        """Expose the logical ChangeSet without losing its staged artifact context."""

        return self.record.changeset


class ChangeSetStore:
    """Creates and loads atomic staging directories without touching stable Wiki."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.staging_dir = workspace.staging_dir

    def create(
        self,
        changeset: ChangeSet,
        candidate_files: Mapping[str, str],
    ) -> StoredChangeSet:
        """Persist a new ChangeSet and its proposed Wiki files exactly once."""

        target = self._directory_for(changeset.changeset_id)
        if target.exists():
            existing = self.get(changeset.changeset_id)
            if (
                existing.changeset == changeset
                and existing.candidate_files == dict(candidate_files)
            ):
                return existing
            raise ChangeSetStoreError(
                f"Refusing to replace staged ChangeSet: {changeset.changeset_id}"
            )

        current_commit = self.workspace.current_commit()
        if changeset.base_commit != current_commit:
            raise ChangeSetStoreError(
                "ChangeSet base_commit does not match the current workspace revision: "
                f"expected {current_commit}, received {changeset.base_commit}"
            )

        record = self._build_record(changeset, candidate_files)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        temporary_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{changeset.changeset_id}.",
                suffix=".tmp",
                dir=self.staging_dir,
            )
        )
        try:
            self._write_record(temporary_directory, record, candidate_files)
            try:
                os.replace(temporary_directory, target)
            except FileExistsError:
                existing = self.get(changeset.changeset_id)
                if existing.changeset == changeset and existing.candidate_files == dict(
                    candidate_files
                ):
                    return existing
                raise ChangeSetStoreError(
                    f"Concurrent staged ChangeSet conflicts with {changeset.changeset_id}"
                ) from None
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)

        return self.get(changeset.changeset_id)

    def get(self, changeset_id: str) -> StoredChangeSet:
        """Load a ChangeSet only when all referenced candidate hashes still match."""

        directory = self._directory_for(changeset_id)
        metadata_path = directory / CHANGESET_FILENAME
        if not metadata_path.is_file():
            raise ChangeSetStoreError(f"Staged ChangeSet does not exist: {changeset_id}")
        try:
            record = StagedChangeSet.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise ChangeSetStoreError(
                f"Staged ChangeSet metadata is invalid: {changeset_id}"
            ) from error
        if record.changeset.changeset_id != changeset_id:
            raise ChangeSetStoreError(
                "Staged ChangeSet directory and metadata IDs do not match: "
                f"{changeset_id} != {record.changeset.changeset_id}"
            )

        candidate_files: dict[str, str] = {}
        for proposed in record.proposed_files:
            candidate_path = _candidate_path(directory / PROPOSED_DIRECTORY, proposed.path)
            try:
                content_bytes = candidate_path.read_bytes()
                content = content_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise ChangeSetStoreError(
                    f"Staged candidate is missing or not UTF-8: {proposed.path}"
                ) from error
            content_sha256 = hashlib.sha256(content_bytes).hexdigest()
            if content_sha256 != proposed.content_sha256:
                raise ChangeSetStoreError(
                    f"Staged candidate hash does not match metadata: {proposed.path}"
                )
            if len(content_bytes) != proposed.byte_count:
                raise ChangeSetStoreError(
                    f"Staged candidate byte count does not match metadata: {proposed.path}"
                )
            candidate_files[proposed.path] = content

        return StoredChangeSet(
            record=record,
            directory=directory,
            candidate_files=candidate_files,
        )

    def list_all(self) -> list[StoredChangeSet]:
        """Return all valid staged ChangeSets in deterministic ID order."""

        if not self.staging_dir.exists():
            return []
        return [
            self.get(directory.name)
            for directory in sorted(self.staging_dir.iterdir())
            if directory.is_dir() and CHANGESET_ID_PATTERN.fullmatch(directory.name)
        ]

    def _build_record(
        self,
        changeset: ChangeSet,
        candidate_files: Mapping[str, str],
    ) -> StagedChangeSet:
        """Hash candidate files before their first write and validate their linkage."""

        proposed_files = tuple(
            StagedWikiFile(
                path=path,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                byte_count=len(content.encode("utf-8")),
            )
            for path, content in sorted(candidate_files.items())
        )
        try:
            return StagedChangeSet(
                staged_at=datetime.now(timezone.utc),
                changeset=changeset,
                proposed_files=proposed_files,
            )
        except ValidationError as error:
            raise ChangeSetStoreError(f"Invalid ChangeSet staging payload: {error}") from error

    def _write_record(
        self,
        directory: Path,
        record: StagedChangeSet,
        candidate_files: Mapping[str, str],
    ) -> None:
        """Write a complete record in a temporary directory before publication."""

        for path, content in candidate_files.items():
            candidate_path = _candidate_path(directory / PROPOSED_DIRECTORY, path)
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text(content, encoding="utf-8")
        metadata_path = directory / CHANGESET_FILENAME
        metadata_path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _directory_for(self, changeset_id: str) -> Path:
        """Map a validated ChangeSet ID to its dedicated staging directory."""

        if not CHANGESET_ID_PATTERN.fullmatch(changeset_id):
            raise ChangeSetStoreError(f"Invalid ChangeSet ID: {changeset_id}")
        return self.staging_dir / changeset_id


def _candidate_path(root: Path, wiki_path: str) -> Path:
    """Translate a validated POSIX Wiki path without allowing filesystem escape."""

    relative_path = PurePosixPath(wiki_path)
    if (
        relative_path.is_absolute()
        or relative_path.parts[0] != "wiki"
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or "\\" in wiki_path
    ):
        raise ChangeSetStoreError(f"Invalid staged Wiki path: {wiki_path}")
    return root.joinpath(*relative_path.parts)
