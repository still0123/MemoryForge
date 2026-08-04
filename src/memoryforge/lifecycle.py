"""Human review state for immutable staged ChangeSets."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.errors import LifecycleError
from memoryforge.models import ChangeSetLifecycle, ChangeSetStatus
from memoryforge.workspace import Workspace

LIFECYCLE_FILENAME = "lifecycle.json"


class ChangeSetLifecycleStore:
    def __init__(self, workspace: Workspace) -> None:
        self.changesets = ChangeSetStore(workspace)

    def ensure_validated(self, changeset_id: str) -> ChangeSetLifecycle:
        stored = self.changesets.get(changeset_id)
        existing = self._read(stored)
        if existing is not None:
            return existing
        return self._write(
            stored,
            ChangeSetLifecycle(
                changeset_id=changeset_id,
                status=ChangeSetStatus.VALIDATED,
                updated_at=datetime.now(UTC),
            ),
        )

    def mark_reviewed(self, changeset_id: str) -> ChangeSetLifecycle:
        stored = self.changesets.get(changeset_id)
        state = self.ensure_validated(changeset_id)
        if state.status is not ChangeSetStatus.VALIDATED:
            raise LifecycleError(f"Cannot review ChangeSet in {state.status.value} state")
        now = datetime.now(UTC)
        return self._write(
            stored,
            state.model_copy(update={"updated_at": now, "reviewed_at": state.reviewed_at or now}),
        )

    def approve(self, changeset_id: str) -> ChangeSetLifecycle:
        stored = self.changesets.get(changeset_id)
        state = self.ensure_validated(changeset_id)
        if state.status is ChangeSetStatus.APPROVED:
            return state
        if state.reviewed_at is None:
            raise LifecycleError("Review the ChangeSet before approval")
        now = datetime.now(UTC)
        return self._write(
            stored,
            state.model_copy(
                update={
                    "status": ChangeSetStatus.APPROVED,
                    "updated_at": now,
                    "approved_at": now,
                }
            ),
        )

    def is_approved(self, changeset_id: str) -> bool:
        stored = self.changesets.get(changeset_id)
        state = self._read(stored)
        return state is not None and state.status is ChangeSetStatus.APPROVED

    @staticmethod
    def _read(stored: StoredChangeSet) -> ChangeSetLifecycle | None:
        path = stored.directory / LIFECYCLE_FILENAME
        if not path.is_file():
            return None
        try:
            state = ChangeSetLifecycle.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise LifecycleError("Invalid ChangeSet lifecycle state") from exc
        if state.changeset_id != stored.changeset.changeset_id:
            raise LifecycleError("ChangeSet lifecycle ID does not match")
        return state

    @staticmethod
    def _write(stored: StoredChangeSet, state: ChangeSetLifecycle) -> ChangeSetLifecycle:
        (stored.directory / LIFECYCLE_FILENAME).write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return state
