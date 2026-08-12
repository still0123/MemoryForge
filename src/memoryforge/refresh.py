"""One manual refresh pass over already registered knowledge sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memoryforge.feishu_adapter import FeishuDocumentSyncResult, refresh_feishu_documents
from memoryforge.models import GitRepositorySyncResult
from memoryforge.workspace import list_git_checkouts, sync_git_checkout


@dataclass(frozen=True)
class RefreshResult:
    git: tuple[GitRepositorySyncResult, ...]
    feishu: tuple[FeishuDocumentSyncResult, ...]

    @property
    def changed(self) -> bool:
        return any(result.created or result.updated for result in self.git) or any(
            result.created or result.updated or result.deleted for result in self.feishu
        )


def refresh_workspace(workspace: Path) -> RefreshResult:
    """Sync local Git snapshots and previously imported Feishu documents once."""
    git = tuple(
        sync_git_checkout(workspace, repository.repository_id)
        for repository in list_git_checkouts(workspace)
    )
    return RefreshResult(git=git, feishu=refresh_feishu_documents(workspace))
