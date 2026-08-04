"""Review, approval, and application services for staged ChangeSets."""

from __future__ import annotations

import difflib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.errors import LifecycleError, WorkspaceError
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import ChangeSetLifecycle, ChangeSetStatus
from memoryforge.workspace import Workspace

LIFECYCLE_FILENAME = "lifecycle.json"


class ChangeSetLifecycleStore:
    """Persists mutable status without rewriting immutable proposal metadata."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.changesets = ChangeSetStore(workspace)

    def ensure_validated(self, changeset_id: str) -> ChangeSetLifecycle:
        """Create the initial validated state after deterministic checks pass."""

        stored = self.changesets.get(changeset_id)
        if stored.changeset.validation is None:
            raise LifecycleError("ChangeSet has no validation result")
        validation = stored.changeset.validation
        if validation.schema_errors or validation.unresolved_conflicts:
            raise LifecycleError("ChangeSet validation blocks review")
        existing = self._read_optional(stored)
        if existing is not None:
            return existing
        state = ChangeSetLifecycle(
            changeset_id=changeset_id,
            status=ChangeSetStatus.VALIDATED,
            updated_at=_now(),
        )
        self._write(stored, state)
        return state

    def get(self, changeset_id: str) -> ChangeSetLifecycle:
        """Load lifecycle state for an existing staged ChangeSet."""

        stored = self.changesets.get(changeset_id)
        state = self._read_optional(stored)
        if state is None:
            raise LifecycleError(f"ChangeSet has no lifecycle state: {changeset_id}")
        return state

    def mark_reviewed(self, changeset_id: str) -> ChangeSetLifecycle:
        """Record that a human displayed the candidate diff."""

        stored = self.changesets.get(changeset_id)
        state = self.get(changeset_id)
        if state.status is not ChangeSetStatus.VALIDATED:
            raise LifecycleError(f"Only VALIDATED ChangeSets can be reviewed: {state.status.value}")
        now = _now()
        reviewed = state.model_copy(
            update={"updated_at": now, "reviewed_at": state.reviewed_at or now}
        )
        self._write(stored, reviewed)
        return reviewed

    def approve(self, changeset_id: str) -> ChangeSetLifecycle:
        """Approve a reviewed ChangeSet without writing the stable Wiki."""

        stored = self.changesets.get(changeset_id)
        state = self.get(changeset_id)
        if state.status is ChangeSetStatus.APPROVED:
            return state
        if state.status is not ChangeSetStatus.VALIDATED:
            raise LifecycleError(f"Cannot approve ChangeSet in state {state.status.value}")
        if state.reviewed_at is None:
            raise LifecycleError("Review the ChangeSet before approval")
        now = _now()
        approved = state.model_copy(
            update={
                "status": ChangeSetStatus.APPROVED,
                "updated_at": now,
                "approved_at": now,
            }
        )
        self._write(stored, approved)
        return approved

    def mark_applied(self, changeset_id: str, commit: str) -> ChangeSetLifecycle:
        """Record the terminal application state after Git commit succeeds."""

        stored = self.changesets.get(changeset_id)
        state = self.get(changeset_id)
        if state.status is not ChangeSetStatus.APPROVED:
            raise LifecycleError(f"Cannot mark ChangeSet applied from {state.status.value}")
        now = _now()
        applied = state.model_copy(
            update={
                "status": ChangeSetStatus.APPLIED,
                "updated_at": now,
                "applied_at": now,
                "applied_commit": commit,
            }
        )
        self._write(stored, applied)
        return applied

    @staticmethod
    def _read_optional(stored: StoredChangeSet) -> ChangeSetLifecycle | None:
        path = stored.directory / LIFECYCLE_FILENAME
        if not path.is_file():
            return None
        try:
            state = ChangeSetLifecycle.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise LifecycleError(
                f"Invalid lifecycle state for {stored.changeset.changeset_id}"
            ) from error
        if state.changeset_id != stored.changeset.changeset_id:
            raise LifecycleError("ChangeSet lifecycle ID does not match its directory")
        return state

    @staticmethod
    def _write(stored: StoredChangeSet, state: ChangeSetLifecycle) -> None:
        destination = stored.directory / LIFECYCLE_FILENAME
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, state.model_dump_json(indent=2) + "\n")


class ChangeSetService:
    """Coordinates human review and safe publication into the stable Wiki."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.changesets = ChangeSetStore(workspace)
        self.lifecycle = ChangeSetLifecycleStore(workspace)
        self.manifests = SourceManifestStore(workspace.manifest_dir)

    def review(self, changeset_id: str) -> str:
        """Return a unified diff and record that it was presented for review."""

        stored = self.changesets.get(changeset_id)
        state = self.lifecycle.get(changeset_id)
        sections = [
            f"ChangeSet: {changeset_id}",
            f"Status: {state.status.value}",
            f"Base commit: {stored.changeset.base_commit}",
            f"Sources: {', '.join(stored.changeset.source_ids)}",
            "",
        ]
        for path, candidate in sorted(stored.candidate_files.items()):
            stable_path = self.workspace.root / path
            existing = (
                stable_path.read_text(encoding="utf-8") if stable_path.is_file() else ""
            )
            diff = difflib.unified_diff(
                existing.splitlines(),
                candidate.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
            sections.extend([*diff, ""])
        self.lifecycle.mark_reviewed(changeset_id)
        return "\n".join(sections).rstrip() + "\n"

    def apply(self, changeset_id: str) -> str:
        """Publish approved candidates and commit exactly their knowledge artifacts."""

        stored = self.changesets.get(changeset_id)
        state = self.lifecycle.get(changeset_id)
        if state.status is ChangeSetStatus.APPLIED and state.applied_commit:
            return state.applied_commit
        if state.status is not ChangeSetStatus.APPROVED:
            raise LifecycleError(f"Cannot apply ChangeSet in state {state.status.value}")
        if self.workspace.current_commit() != stored.changeset.base_commit:
            raise LifecycleError("ChangeSet base commit is stale; compile a new ChangeSet")

        wiki_paths = tuple(sorted(stored.candidate_files))
        if not self.workspace.version_store.paths_are_clean(wiki_paths):
            raise LifecycleError("Stable Wiki has uncommitted changes; refusing to overwrite")

        backups: dict[Path, bytes | None] = {}
        commit_paths: tuple[str, ...] = ()
        try:
            for relative_path, content in stored.candidate_files.items():
                destination = self.workspace.root / relative_path
                backups[destination] = destination.read_bytes() if destination.exists() else None
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(destination, content)

            source_paths = self._source_paths(stored)
            commit_paths = tuple(sorted({*wiki_paths, *source_paths}))
            commit = self.workspace.version_store.commit(
                commit_paths,
                f"knowledge: apply {changeset_id}",
            )
        except Exception as error:
            self._restore(backups)
            self.workspace.version_store.unstage(commit_paths)
            if isinstance(error, (LifecycleError, WorkspaceError)):
                raise
            raise LifecycleError(f"Failed to apply ChangeSet: {error}") from error

        self.lifecycle.mark_applied(changeset_id, commit)
        return commit

    def _source_paths(self, stored: StoredChangeSet) -> tuple[str, ...]:
        manifests = {source.source_id: source for source in self.manifests.list_all()}
        paths: list[str] = []
        for source_id in stored.changeset.source_ids:
            source = manifests.get(source_id)
            if source is None:
                raise LifecycleError(f"Missing source manifest: {source_id}")
            paths.extend(
                [
                    source.uri,
                    f".memoryforge/manifests/sources/{source_id}.json",
                ]
            )
        return tuple(paths)

    @staticmethod
    def _restore(backups: dict[Path, bytes | None]) -> None:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)


def _atomic_write(destination: Path, content: str) -> None:
    """Replace one UTF-8 file atomically within its destination directory."""

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)
