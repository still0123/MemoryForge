"""Durable, immutable staging storage for proposed Wiki modifications."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import ValidationError

from memoryforge.core.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.core.models import (
    ApprovalReceipt,
    AutomationDecisionReceipt,
    ChangeOrigin,
    ChangeSet,
    ChangeSetStatus,
    ReviewActorType,
    ReviewReceipt,
    RiskLevel,
    StagedChangeSet,
    StagedWikiFile,
)
from memoryforge.core.platform_lock import exclusive_posix_directory_lock
from memoryforge.storage.workspace import Workspace, WorkspaceIntegrityError, _connect

CHANGESET_ID_PATTERN = re.compile(r"^chg_[A-Za-z0-9_-]+$")
CHANGESET_FILENAME = "changeset.json"
CHANGESET_DIGEST_FILENAME = "changeset.sha256"
PROPOSED_DIRECTORY = "proposed"
REVIEW_FILENAME = "review.json"
REVIEW_DIGEST_FILENAME = "review.sha256"
APPROVAL_FILENAME = "approval.json"
APPROVAL_DIGEST_FILENAME = "approval.sha256"
DECISION_FILENAME = "decision.json"
DECISION_DIGEST_FILENAME = "decision.sha256"


@dataclass(frozen=True)
class StoredChangeSet:
    record: StagedChangeSet
    directory: Path
    candidate_files: dict[str, str]
    proposal_sha256: str

    @property
    def changeset(self) -> ChangeSet:
        return self.record.changeset


def _proposal_drafts(
    changeset: ChangeSet,
    candidate_files: Mapping[str, str],
) -> tuple[object, ...]:
    from memoryforge.compiler.knowledge_conflicts import ProposalDraft

    origins = {operation.path: operation.origin for operation in changeset.operations}
    return tuple(
        ProposalDraft(
            page_path=path,
            content=content,
            citations=changeset.claims,
            origin=origins.get(path) or ChangeOrigin.AGENT_PROPOSAL,
            risk=RiskLevel.HIGH,
        )
        for path, content in sorted(candidate_files.items())
        if path.startswith("wiki/pages/") and path.endswith(".md")
    )


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
        temp_name = f".{changeset.changeset_id}.{uuid.uuid4().hex}.tmp"
        detected_conflicts = ()
        try:
            with self._locked_staging() as staging_fd:
                current_commit = self.workspace.current_commit()
                if changeset.base_commit != current_commit:
                    raise ChangeSetStoreError(
                        "ChangeSet base_commit does not match the current workspace revision: "
                        f"expected {current_commit}, received {changeset.base_commit}"
                    )
                if _entry_exists(staging_fd, changeset.changeset_id):
                    return self._require_idempotent(
                        changeset.changeset_id, changeset, candidate_files
                    )
                try:
                    self.workspace.validate_changeset_evidence(changeset)
                except (WorkspaceError, WorkspaceIntegrityError, ValueError) as exc:
                    raise ChangeSetStoreError(
                        f"ChangeSet source or citation evidence is invalid: {exc}"
                    ) from exc

                record = self._build_record(changeset, candidate_files)
                detected_conflicts = self._detect_conflicts(changeset, candidate_files)
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
                    return self._require_idempotent(
                        changeset.changeset_id, changeset, candidate_files
                    )
        except OSError as exc:
            raise ChangeSetStoreError("ChangeSet could not be published safely") from exc
        finally:
            temporary_path = self.staging_dir / temp_name
            if temporary_path.exists() and not temporary_path.is_symlink():
                shutil.rmtree(temporary_path)
        stored = self.get(changeset.changeset_id)
        if detected_conflicts:
            from memoryforge.compiler.knowledge_conflicts import persist_conflicts

            with _connect(self.workspace.index_path) as connection:
                persist_conflicts(connection, detected_conflicts)
        return stored

    def get(self, changeset_id: str) -> StoredChangeSet:
        return self._get(changeset_id, require_current_base=True)

    def get_for_recovery(self, changeset_id: str) -> StoredChangeSet:
        """Read an immutable proposal after its apply Commit advanced HEAD."""
        return self._get(changeset_id, require_current_base=False)

    def _get(self, changeset_id: str, *, require_current_base: bool) -> StoredChangeSet:
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
                if require_current_base:
                    self._require_current_base(record.changeset)
            finally:
                os.close(directory_fd)
        finally:
            os.close(staging_fd)
        return StoredChangeSet(
            record,
            self.staging_dir / changeset_id,
            candidate_files,
            hashlib.sha256(metadata).hexdigest(),
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

    def record_review(
        self,
        stored: StoredChangeSet,
        *,
        mode: Literal["displayed", "inline_legacy", "policy"] = "displayed",
        actor_type: ReviewActorType = ReviewActorType.HUMAN,
        actor_id: str = "human",
        decision_sha256: str | None = None,
    ) -> ReviewReceipt:
        """Bind a review event (human-visible or policy-driven) to the immutable proposal."""

        review = ReviewReceipt(
            changeset_id=stored.changeset.changeset_id,
            proposal_sha256=stored.proposal_sha256,
            review_mode=mode,
            actor_type=actor_type,
            actor_id=actor_id,
            decision_sha256=decision_sha256,
            reviewed_at=datetime.now(UTC),
        )
        existing = self._read_review(stored)
        if existing is not None:
            return existing
        self._write_receipt(
            stored,
            REVIEW_FILENAME,
            REVIEW_DIGEST_FILENAME,
            (review.model_dump_json(indent=2) + "\n").encode(),
        )
        return self._require_review(stored)

    def approve(
        self,
        stored: StoredChangeSet,
        *,
        actor_type: ReviewActorType = ReviewActorType.HUMAN,
        actor_id: str = "human",
        decision_sha256: str | None = None,
        policy_sha256: str | None = None,
        approval_reason_codes: tuple[str, ...] = (),
    ) -> ApprovalReceipt:
        """Approve an already reviewed proposal without changing stable Wiki files."""

        review_payload, review = self._require_review_payload(stored)
        existing = self._read_approval(stored)
        if existing is not None:
            return existing
        approval = ApprovalReceipt(
            changeset_id=stored.changeset.changeset_id,
            proposal_sha256=stored.proposal_sha256,
            review_sha256=hashlib.sha256(review_payload).hexdigest(),
            actor_type=actor_type,
            actor_id=actor_id,
            decision_sha256=decision_sha256,
            policy_sha256=policy_sha256,
            approval_reason_codes=approval_reason_codes,
            approved_at=datetime.now(UTC),
        )
        self._write_receipt(
            stored,
            APPROVAL_FILENAME,
            APPROVAL_DIGEST_FILENAME,
            (approval.model_dump_json(indent=2) + "\n").encode(),
        )
        return self.require_approved(stored)

    def write_decision(
        self,
        stored: StoredChangeSet,
        receipt: AutomationDecisionReceipt,
    ) -> AutomationDecisionReceipt:
        """Persist a bound automation decision receipt for one staged proposal."""

        existing = self._read_decision(stored)
        if existing is not None:
            return existing
        self._write_receipt(
            stored,
            DECISION_FILENAME,
            DECISION_DIGEST_FILENAME,
            (receipt.model_dump_json(indent=2) + "\n").encode(),
        )
        return self._require_decision(stored)

    def read_decision(self, stored: StoredChangeSet) -> AutomationDecisionReceipt | None:
        """Return the recorded automation decision receipt, if any."""

        return self._read_decision(stored)

    def require_approved(self, stored: StoredChangeSet) -> ApprovalReceipt:
        """Return the bound approval receipt or fail closed."""

        review_payload, _ = self._require_review_payload(stored)
        approval = self._read_approval(stored)
        if approval is None:
            raise ChangeSetStoreError(
                f"ChangeSet requires approval before apply: {stored.changeset.changeset_id}"
            )
        if approval.review_sha256 != hashlib.sha256(review_payload).hexdigest():
            raise ChangeSetStoreError("Approval receipt does not match the recorded review")
        return approval

    def archive_applied(self, stored: StoredChangeSet, *, commit: str) -> Path:
        """Move an applied proposal out of the pending staging namespace."""
        try:
            with self._locked_staging() as staging_fd:
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
        return self.staging_dir / "applied" / stored.changeset.changeset_id

    def ensure_applied_receipt(self, changeset_id: str, *, commit: str) -> Path:
        """Complete or verify one applied archive after an interrupted rename."""
        _validate_changeset_id(changeset_id)
        applied = self.staging_dir / "applied"
        self.workspace.validate_internal_directory(applied)
        applied_fd = os.open(
            applied,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            archived_fd = os.open(
                changeset_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=applied_fd,
            )
            try:
                receipt = {
                    "status": "APPLIED",
                    "commit": commit,
                    "changeset_id": changeset_id,
                }
                if _entry_exists(archived_fd, "receipt.json"):
                    recorded = json.loads(_read_regular_file(archived_fd, "receipt.json"))
                    if any(recorded.get(key) != value for key, value in receipt.items()):
                        raise ChangeSetStoreError("Applied ChangeSet receipt is invalid")
                else:
                    receipt["applied_at"] = datetime.now(UTC).isoformat()
                    _write_new_file(
                        archived_fd,
                        "receipt.json",
                        (
                            json.dumps(
                                receipt,
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            )
                            + "\n"
                        ).encode(),
                    )
            finally:
                os.close(archived_fd)
        except OSError as exc:
            raise ChangeSetStoreError("Applied ChangeSet receipt could not be recovered") from exc
        finally:
            os.close(applied_fd)
        return applied / changeset_id

    def archive_rejected(self, stored: StoredChangeSet) -> Path:
        """Move a rejected proposal out of the pending staging namespace."""
        try:
            with self._locked_staging() as staging_fd:
                with suppress(FileExistsError):
                    os.mkdir("rejected", 0o700, dir_fd=staging_fd)
                rejected_fd = os.open(
                    "rejected",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=staging_fd,
                )
                try:
                    os.rename(
                        stored.changeset.changeset_id,
                        stored.changeset.changeset_id,
                        src_dir_fd=staging_fd,
                        dst_dir_fd=rejected_fd,
                    )
                    os.fsync(rejected_fd)
                    os.fsync(staging_fd)
                finally:
                    os.close(rejected_fd)
        except OSError as exc:
            raise ChangeSetStoreError("Rejected ChangeSet could not be archived") from exc
        return self.staging_dir / "rejected" / stored.changeset.changeset_id

    def _require_current_base(self, changeset: ChangeSet) -> None:
        current_commit = self.workspace.current_commit()
        if changeset.base_commit != current_commit:
            raise ChangeSetStoreError(
                "Staged ChangeSet base_commit does not match the current workspace revision: "
                f"expected {current_commit}, received {changeset.base_commit}"
            )

    def _read_review(self, stored: StoredChangeSet) -> ReviewReceipt | None:
        payload = self._read_receipt(stored, REVIEW_FILENAME, REVIEW_DIGEST_FILENAME)
        if payload is None:
            return None
        try:
            receipt = ReviewReceipt.model_validate_json(payload)
        except ValidationError as exc:
            raise ChangeSetStoreError("Staged ChangeSet review receipt is invalid") from exc
        self._require_receipt_binding(
            stored,
            changeset_id=receipt.changeset_id,
            proposal_sha256=receipt.proposal_sha256,
        )
        return receipt

    def _require_review(self, stored: StoredChangeSet) -> ReviewReceipt:
        _, review = self._require_review_payload(stored)
        return review

    def _require_review_payload(
        self,
        stored: StoredChangeSet,
    ) -> tuple[bytes, ReviewReceipt]:
        payload = self._read_receipt(stored, REVIEW_FILENAME, REVIEW_DIGEST_FILENAME)
        if payload is None:
            raise ChangeSetStoreError(
                f"Review the ChangeSet before approval: {stored.changeset.changeset_id}"
            )
        try:
            review = ReviewReceipt.model_validate_json(payload)
        except ValidationError as exc:
            raise ChangeSetStoreError("Staged ChangeSet review receipt is invalid") from exc
        self._require_receipt_binding(
            stored,
            changeset_id=review.changeset_id,
            proposal_sha256=review.proposal_sha256,
        )
        return payload, review

    def _read_approval(self, stored: StoredChangeSet) -> ApprovalReceipt | None:
        payload = self._read_receipt(stored, APPROVAL_FILENAME, APPROVAL_DIGEST_FILENAME)
        if payload is None:
            return None
        try:
            receipt = ApprovalReceipt.model_validate_json(payload)
        except ValidationError as exc:
            raise ChangeSetStoreError("Staged ChangeSet approval receipt is invalid") from exc
        self._require_receipt_binding(
            stored,
            changeset_id=receipt.changeset_id,
            proposal_sha256=receipt.proposal_sha256,
        )
        return receipt

    def _read_decision(
        self,
        stored: StoredChangeSet,
    ) -> AutomationDecisionReceipt | None:
        payload = self._read_receipt(stored, DECISION_FILENAME, DECISION_DIGEST_FILENAME)
        if payload is None:
            return None
        try:
            receipt = AutomationDecisionReceipt.model_validate_json(payload)
        except ValidationError as exc:
            raise ChangeSetStoreError("Staged ChangeSet decision receipt is invalid") from exc
        self._require_receipt_binding(
            stored,
            changeset_id=receipt.changeset_id,
            proposal_sha256=receipt.proposal_sha256,
        )
        return receipt

    def _require_decision(self, stored: StoredChangeSet) -> AutomationDecisionReceipt:
        receipt = self._read_decision(stored)
        if receipt is None:
            raise ChangeSetStoreError(
                f"ChangeSet has no recorded automation decision: {stored.changeset.changeset_id}"
            )
        return receipt

    @staticmethod
    def _require_receipt_binding(
        stored: StoredChangeSet,
        *,
        changeset_id: str,
        proposal_sha256: str,
    ) -> None:
        if (
            changeset_id != stored.changeset.changeset_id
            or proposal_sha256 != stored.proposal_sha256
        ):
            raise ChangeSetStoreError("Lifecycle receipt does not match the staged ChangeSet")

    def _read_receipt(
        self,
        stored: StoredChangeSet,
        filename: str,
        digest_filename: str,
    ) -> bytes | None:
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        try:
            directory_fd = os.open(
                stored.changeset.changeset_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            try:
                if not _entry_exists(directory_fd, filename):
                    if _entry_exists(directory_fd, digest_filename):
                        raise ChangeSetStoreError("Lifecycle receipt is incomplete")
                    return None
                payload = _read_regular_file(directory_fd, filename)
                digest = _read_regular_file(directory_fd, digest_filename).decode("ascii")
                if digest != hashlib.sha256(payload).hexdigest() + "\n":
                    raise ChangeSetStoreError("Lifecycle receipt integrity check failed")
                return payload
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ChangeSetStoreError("Lifecycle receipt could not be read safely") from exc
        finally:
            os.close(staging_fd)

    def _write_receipt(
        self,
        stored: StoredChangeSet,
        filename: str,
        digest_filename: str,
        payload: bytes,
    ) -> None:
        try:
            with self._locked_staging() as staging_fd:
                directory_fd = os.open(
                    stored.changeset.changeset_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=staging_fd,
                )
                try:
                    metadata = _read_regular_file(directory_fd, CHANGESET_FILENAME)
                    if hashlib.sha256(metadata).hexdigest() != stored.proposal_sha256:
                        raise ChangeSetStoreError(
                            "Lifecycle receipt cannot bind to modified proposal metadata"
                        )
                    _write_new_file(directory_fd, filename, payload)
                    _write_new_file(
                        directory_fd,
                        digest_filename,
                        (hashlib.sha256(payload).hexdigest() + "\n").encode("ascii"),
                    )
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except FileExistsError:
            return
        except OSError as exc:
            raise ChangeSetStoreError("Lifecycle receipt could not be written safely") from exc

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

    def _detect_conflicts(
        self, changeset: ChangeSet, candidate_files: Mapping[str, str]
    ) -> tuple[object, ...]:
        from memoryforge.compiler.knowledge_conflicts import detect_conflicts

        pending = [
            draft
            for stored in self._list_all_for_conflict_detection()
            if stored.changeset.base_commit == changeset.base_commit
            for draft in _proposal_drafts(stored.changeset, stored.candidate_files)
        ]
        conflicts = []
        for candidate in _proposal_drafts(changeset, candidate_files):
            conflicts.extend(
                detect_conflicts(
                    candidate=candidate,
                    existing_claims=changeset.claims,
                    pending=pending,
                )
            )
        return tuple({conflict.conflict_id: conflict for conflict in conflicts}.values())

    def _list_all_for_conflict_detection(self) -> list[StoredChangeSet]:
        self.workspace.validate_internal_directory(self.staging_dir)
        staging_fd = self._open_staging()
        try:
            identifiers = sorted(
                name for name in os.listdir(staging_fd) if CHANGESET_ID_PATTERN.fullmatch(name)
            )
        finally:
            os.close(staging_fd)
        return [self.get_for_recovery(identifier) for identifier in identifiers]

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

    @contextmanager
    def _locked_staging(self) -> Iterator[int]:
        self.workspace.validate_internal_directory(self.staging_dir)
        with exclusive_posix_directory_lock(self.staging_dir) as descriptor:
            yield descriptor


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
