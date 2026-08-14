"""One manual refresh pass over already registered knowledge sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from memoryforge.adapters.feishu_adapter import FeishuDocumentSyncResult, refresh_feishu_documents
from memoryforge.adapters.git_sync import sync_git_checkout
from memoryforge.core.models import GitRepositorySyncResult
from memoryforge.storage.workspace import list_git_checkouts


@dataclass(frozen=True)
class RefreshResult:
    git: tuple[GitRepositorySyncResult, ...]
    feishu: tuple[FeishuDocumentSyncResult, ...]

    @property
    def changed(self) -> bool:
        return any(result.created or result.updated for result in self.git) or any(
            result.created or result.updated or result.deleted for result in self.feishu
        )


def route_refresh_impact(
    changed_sources: tuple[tuple[str, int], ...],
    *,
    source_to_pages: Mapping[tuple[str, int], tuple[str, ...]],
    code_dependents: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """薄钩子 → freshness.impacted_pages_for_refresh；暂不主动调用。"""
    try:
        from memoryforge.compiler.freshness import impacted_pages_for_refresh

        return impacted_pages_for_refresh(
            changed_sources,
            source_to_pages=source_to_pages,
            code_dependents=code_dependents,
        )
    except Exception:
        return tuple(sorted({p for pages in source_to_pages.values() for p in pages}))


def refresh_workspace(workspace: Path) -> RefreshResult:
    """Sync local Git snapshots and previously imported Feishu documents once."""
    git = tuple(
        sync_git_checkout(workspace, repository.repository_id)
        for repository in list_git_checkouts(workspace)
    )
    return RefreshResult(git=git, feishu=refresh_feishu_documents(workspace))
