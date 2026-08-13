"""Shared review, approval, apply, and rejection workflows."""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from memoryforge.apply_journal import ApplyJournalStore
from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.errors import MemoryForgeError
from memoryforge.linting import lint_workspace
from memoryforge.models import ChangeOperationType
from memoryforge.obsidian import build_obsidian
from memoryforge.showcase import _markdown_document
from memoryforge.wiki_facts import IndexedWikiFact, parse_page_facts
from memoryforge.workspace import (
    Workspace,
    _connect_readonly,
    candidate_page_sources,
    validate_candidate_page_evidence,
    validate_changeset_page_sources,
)

_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_INTERNAL_ID = re.compile(r"\b[a-f0-9]{64}\b")


def list_updates(workspace: Path) -> list[dict[str, Any]]:
    opened = Workspace.open_readonly(workspace)
    return [_update_summary(opened, stored) for stored in ChangeSetStore(opened).list_all()]


def get_update(workspace: Path, changeset_id: str) -> dict[str, Any]:
    opened = Workspace.open_readonly(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    return _update_details(opened, stored)


def review_changeset(workspace: Path, changeset_id: str) -> dict[str, Any]:
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        store = ChangeSetStore(opened)
        stored = store.get(changeset_id)
        details = _update_details(opened, stored)
        receipt = store.record_review(stored)
    return {
        **details,
        "changeset_id": stored.changeset.changeset_id,
        "status": stored.changeset.status.value,
        "reviewed_at": receipt.reviewed_at.isoformat(),
        "candidate_files": stored.candidate_files,
        "unified_diff": {
            item["path"]: item["diff"] for item in details["pages"]
        },
    }


def approve_changeset(workspace: Path, changeset_id: str) -> dict[str, Any]:
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        store = ChangeSetStore(opened)
        approval = store.approve(store.get(changeset_id))
    return {
        "changeset_id": changeset_id,
        "status": approval.status,
        "approved_at": approval.approved_at.isoformat(),
    }


def apply_changeset(
    workspace: Path,
    changeset_id: str,
    *,
    review_mode: str | None = None,
    obsidian_builder: Callable[[Path], dict[str, object]] = build_obsidian,
) -> dict[str, Any]:
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        store = ChangeSetStore(opened)
        stored = store.get(changeset_id)
        if review_mode is not None:
            if review_mode != "displayed":
                raise ValueError("unsupported review mode")
            store.record_review(stored, mode="displayed")
            store.approve(stored)
        else:
            store.require_approved(stored)
        return _apply_stored(opened, store, stored, obsidian_builder=obsidian_builder)


def reject_changeset(workspace: Path, changeset_id: str) -> dict[str, str]:
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        store = ChangeSetStore(opened)
        store.archive_rejected(store.get_for_recovery(changeset_id))
    return {"changeset_id": changeset_id, "status": "REJECTED"}


def _apply_stored(
    opened: Workspace,
    store: ChangeSetStore,
    stored: StoredChangeSet,
    *,
    obsidian_builder: Callable[[Path], dict[str, object]],
) -> dict[str, Any]:
    archive_paths = tuple(
        sorted(
            operation.path
            for operation in stored.changeset.operations
            if operation.type is ChangeOperationType.ARCHIVE_PAGE
        )
    )
    if any(not path.startswith("wiki/pages/") for path in archive_paths):
        raise ValueError("ARCHIVE_PAGE operations must target wiki/pages/")
    existing_archive_paths = tuple(
        path for path in archive_paths if (opened.root / path).is_file()
    )
    paths = tuple(sorted(set(stored.candidate_files) | set(existing_archive_paths)))
    opened.version_store.require_clean_paths(paths)
    opened.require_current_source_versions(stored.changeset.source_versions)
    validate_candidate_page_evidence(opened, stored.candidate_files)
    page_sources = candidate_page_sources(stored.candidate_files)
    validate_changeset_page_sources(page_sources, stored.changeset.source_ids)
    page_facts = {
        path: parse_page_facts(path, content)
        for path, content in stored.candidate_files.items()
        if path.startswith("wiki/pages/") and path.endswith(".md")
    }
    page_facts.update({path: () for path in archive_paths})
    journal_store = ApplyJournalStore(opened)
    journal = journal_store.prepare(
        stored.changeset.changeset_id,
        stored.changeset.base_commit,
        paths,
    )
    previous_source_versions = opened.record_applied_source_versions(
        stored.changeset.source_versions
    )
    previous_page_sources: dict[str, tuple[str, ...]] = {}
    previous_page_facts: dict[str, tuple[IndexedWikiFact, ...]] = {}
    try:
        previous_page_sources = opened.replace_applied_page_sources(page_sources)
        previous_page_sources.update(opened.remove_applied_page_sources(archive_paths))
        previous_page_facts = opened.replace_applied_page_facts(page_facts)
    except Exception:
        opened.restore_applied_source_versions(previous_source_versions)
        opened.restore_applied_page_sources(previous_page_sources)
        opened.restore_applied_page_facts(previous_page_facts)
        journal_store.clear()
        raise

    previous_files: dict[Path, str | None] = {}
    try:
        for path in existing_archive_paths:
            destination = opened.root / path
            previous_files[destination] = destination.read_text(encoding="utf-8")
            destination.unlink()
        for path, content in stored.candidate_files.items():
            destination = opened.root / path
            previous_files[destination] = (
                destination.read_text(encoding="utf-8") if destination.is_file() else None
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        lint = lint_workspace(opened.root)
        commit = opened.version_store.commit_paths(
            paths,
            f"knowledge: apply {stored.changeset.changeset_id}",
        )
    except Exception:
        for destination, previous in previous_files.items():
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_text(previous, encoding="utf-8")
        opened.restore_applied_source_versions(previous_source_versions)
        opened.restore_applied_page_sources(previous_page_sources)
        opened.restore_applied_page_facts(previous_page_facts)
        with suppress(MemoryForgeError):
            opened.version_store.reset_paths(paths)
        journal_store.clear()
        raise

    journal_store.mark_committed(journal, commit)
    archive_warning = None
    try:
        store.archive_applied(stored, commit=commit)
        journal_store.clear()
    except MemoryForgeError as exc:
        archive_warning = str(exc)
    try:
        obsidian_builder(opened.root)
        obsidian = {"status": "built", "warning": None}
    except Exception as exc:
        obsidian = {
            "status": "failed",
            "warning": f"Wiki applied successfully, but Obsidian rebuild failed: {exc}",
        }
    return {
        "changeset_id": stored.changeset.changeset_id,
        "status": "APPLIED",
        "commit": commit,
        "files": list(paths),
        "warning": archive_warning,
        "obsidian": obsidian,
        "lint": lint,
    }


def _update_summary(opened: Workspace, stored: StoredChangeSet) -> dict[str, Any]:
    counts = {"create": 0, "update": 0, "delete": 0}
    for operation in stored.changeset.operations:
        if operation.type is ChangeOperationType.CREATE_PAGE:
            counts["create"] += 1
        elif operation.type is ChangeOperationType.ARCHIVE_PAGE:
            counts["delete"] += 1
        else:
            counts["update"] += 1
    sources = _source_details(opened, stored)
    name = sources[0]["name"] if sources else "知识结构更新"
    if len(sources) > 1:
        name += f" 等 {len(sources)} 个来源"
    return {
        "id": stored.changeset.changeset_id,
        "name": name,
        "status": "等待审核",
        "counts": counts,
        "created_at": stored.record.staged_at.isoformat(),
        "base_commit": stored.changeset.base_commit[:12],
    }


def _update_details(opened: Workspace, stored: StoredChangeSet) -> dict[str, Any]:
    summary = _update_summary(opened, stored)
    operations = {operation.path: operation.type for operation in stored.changeset.operations}
    page_paths = sorted(set(operations) | set(stored.candidate_files))
    pages = []
    for path in page_paths:
        before = opened.version_store.read_text_at(stored.changeset.base_commit, path) or ""
        after = stored.candidate_files.get(path, "")
        operation = operations.get(path)
        action = (
            "删除"
            if operation is ChangeOperationType.ARCHIVE_PAGE
            else "新增"
            if operation is ChangeOperationType.CREATE_PAGE
            else "修改"
        )
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=path,
                tofile=f"{path} (proposed)",
            )
        )
        pages.append(
            {
                "path": path,
                "title": _page_title(after or before, path),
                "action": action,
                "diff": _redact_internal_ids(diff),
                "citation_count": after.count("[^"),
            }
        )
    validation = stored.changeset.validation
    warnings = []
    if validation is not None:
        if validation.unresolved_conflicts:
            warnings.append(f"{validation.unresolved_conflicts} 个未解决冲突")
        if validation.schema_errors:
            warnings.append(f"{validation.schema_errors} 个结构错误")
    return {
        **summary,
        "workspace_commit": opened.current_commit(),
        "sources": _source_details(opened, stored),
        "pages": pages,
        "warnings": warnings,
    }


def _source_details(opened: Workspace, stored: StoredChangeSet) -> list[dict[str, Any]]:
    if not stored.changeset.source_versions:
        return []
    placeholders = ", ".join("?" for _ in stored.changeset.source_versions)
    parameters = tuple(stored.changeset.source_versions)
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            f"""
            SELECT sources.source_id, versions.id, versions.title,
                   versions.sensitivity, versions.observed_at
            FROM sources
            JOIN source_versions AS versions ON versions.source_id = sources.id
            WHERE sources.source_id IN ({placeholders})
            ORDER BY versions.title, sources.source_id
            """,
            parameters,
        ).fetchall()
    return [
        {
            "name": str(row["title"]),
            "version": int(row["id"]),
            "privacy": str(row["sensitivity"]),
            "updated": str(row["observed_at"]),
        }
        for row in rows
        if stored.changeset.source_versions.get(str(row["source_id"])) == int(row["id"])
    ]


def _page_title(content: str, path: str) -> str:
    metadata, body = _markdown_document(content)
    match = _TITLE.search(body)
    return metadata.get("title") or (match.group(1) if match else Path(path).stem)


def _redact_internal_ids(value: str) -> str:
    return _INTERNAL_ID.sub(lambda match: match.group(0)[:8] + "…", value)
