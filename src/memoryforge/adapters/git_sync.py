"""Git checkout synchronization orchestration."""

from __future__ import annotations

import hashlib
from pathlib import Path

from memoryforge.adapters.git_adapter import (
    CODE_WIKI_VERSION,
    scan_git_snapshot_code,
    scan_git_snapshot_documentation,
    snapshot_git_repository,
)
from memoryforge.adapters.importer import (
    SourceValidationError,
    import_local_document,
    validate_local_document,
)
from memoryforge.core.errors import WorkspaceError
from memoryforge.core.models import (
    GitDocumentSyncResult,
    GitRepositorySyncResult,
    LocalDocument,
)
from memoryforge.storage.database import connect
from memoryforge.storage.workspace import (
    Workspace,
    _current_git_paths,
    _get_git_repository,
    _reconcile_git_snapshot_sources,
    _record_git_source_revision,
    list_git_code_modules,
)


def sync_git_checkout(workspace: Path, repository_id: str) -> GitRepositorySyncResult:
    """Import committed documentation from one registered local checkout."""
    opened = Workspace.open(workspace)
    repository = _get_git_repository(opened, repository_id)
    snapshot = snapshot_git_repository(Path(repository.checkout_path))
    if str(snapshot.repository_root) != repository.checkout_path:
        raise WorkspaceError("registered Git checkout path no longer matches its repository root")
    current_repository_id = hashlib.sha256(snapshot.repository_identity.encode("utf-8")).hexdigest()
    if current_repository_id != repository.repository_id:
        raise WorkspaceError("registered Git checkout identity changed; add it as a new checkout")

    scanned_documents = list(
        scan_git_snapshot_documentation(
            snapshot,
            sensitivity=repository.sensitivity,
        )
    )
    scanned_documents.extend(
        scan_git_snapshot_code(
            snapshot,
            list_git_code_modules(opened, repository.repository_id),
            sensitivity=repository.sensitivity,
        )
    )
    scanned_documents = sorted(
        {document.source_path: document for document in scanned_documents}.values(),
        key=lambda document: document.source_path,
    )
    reusable_paths = (
        _current_git_paths(
            opened,
            repository.repository_id,
            snapshot.revision,
            repository.sensitivity,
            code_wiki_version=CODE_WIKI_VERSION,
        )
        if repository.last_synced_commit == snapshot.revision
        else set()
    )
    safe_documents = []
    skipped = []

    def can_reuse(document: LocalDocument) -> bool:
        return not document.source_path.startswith(".memoryforge/code-modules/") and (
            "code" not in document.tags or CODE_WIKI_VERSION in document.tags
        )

    for document in scanned_documents:
        if document.source_path in reusable_paths and can_reuse(document):
            safe_documents.append(document)
            continue
        try:
            validate_local_document(document)
        except SourceValidationError:
            if "code" not in document.tags:
                raise
            skipped.append(document.source_path)
            continue
        safe_documents.append(document)
    scanned_documents = safe_documents

    documents: list[GitDocumentSyncResult] = []
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for document in scanned_documents:
        source_id = hashlib.sha256(
            f"{repository.repository_id}\0{document.source_path}".encode()
        ).hexdigest()
        if document.source_path in reusable_paths and can_reuse(document):
            counts["unchanged"] += 1
            documents.append(
                GitDocumentSyncResult(
                    source_id=source_id,
                    relative_path=document.source_path,
                    revision=snapshot.revision,
                    status="unchanged",
                )
            )
            continue
        imported = import_local_document(opened.root, document, source_id=source_id)
        _record_git_source_revision(
            opened,
            source_id=imported.source_id,
            repository_id=repository.repository_id,
            relative_path=document.source_path,
            commit_sha=snapshot.revision,
        )
        counts[imported.status] += 1
        documents.append(
            GitDocumentSyncResult(
                source_id=imported.source_id,
                relative_path=document.source_path,
                revision=snapshot.revision,
                status=imported.status,
            )
        )

    _reconcile_git_snapshot_sources(
        opened,
        repository_id=repository.repository_id,
        current_paths={document.source_path for document in scanned_documents},
    )
    with connect(opened.index_path) as connection:
        connection.execute(
            """
            UPDATE git_repositories
            SET last_synced_commit = ?
            WHERE repository_id = ?
            """,
            (snapshot.revision, repository.repository_id),
        )
    return GitRepositorySyncResult(
        repository_id=repository.repository_id,
        head_commit=snapshot.revision,
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        skipped=tuple(skipped),
        documents=tuple(documents),
    )
