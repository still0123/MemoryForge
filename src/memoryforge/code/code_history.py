"""Local Git history introspection for code change rationale."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CodeHistoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "partial", "error"]
    commit_sha: str | None = None
    relative_path: str | None = None
    symbol: str | None = None
    commits: tuple[dict[str, Any], ...] = ()
    partial_reasons: tuple[str, ...] = ()


def _run_git(checkout: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _verify_commit(checkout: Path, commit_sha: str) -> bool:
    result = _run_git(checkout, ["rev-parse", "--verify", f"{commit_sha}^{{commit}}"])
    return result.returncode == 0 and result.stdout.strip() != ""


def why_changed(
    checkout: Path,
    *,
    commit_sha: str,
    relative_path: str,
    symbol: str | None = None,
    max_commits: int = 5,
) -> CodeHistoryResult:
    partial_reasons: list[str] = []

    if not _verify_commit(checkout, commit_sha):
        return CodeHistoryResult(
            status="partial",
            commit_sha=commit_sha,
            relative_path=relative_path,
            symbol=symbol,
            partial_reasons=("commit_unreachable",),
        )

    log_limit = max_commits * 3
    result = _run_git(
        checkout,
        [
            "log",
            "--format=%H %at %s",
            "--follow",
            "-n",
            str(log_limit),
            "--",
            relative_path,
        ],
    )

    if result.returncode != 0:
        return CodeHistoryResult(
            status="error",
            commit_sha=commit_sha,
            relative_path=relative_path,
            symbol=symbol,
            partial_reasons=("git_log_failed",),
        )

    commits_list: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        sha, at_str, subject = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
        try:
            author_time_int = int(at_str)
        except ValueError:
            continue
        name_status = _run_git(
            checkout,
            [
                "show",
                "--name-only",
                "--format=",
                sha,
                "--",
            ],
        )
        changed_paths: list[str] = []
        if name_status.returncode == 0:
            changed_paths = [p for p in name_status.stdout.splitlines() if p.strip()]

        author_time = datetime.fromtimestamp(author_time_int, tz=timezone.utc).isoformat()
        commits_list.append(
            {
                "sha": sha,
                "author_time": author_time,
                "subject": subject,
                "changed_paths": changed_paths,
            }
        )
        if len(commits_list) >= max_commits:
            break

    return CodeHistoryResult(
        status="answered" if commits_list else "partial",
        commit_sha=commit_sha,
        relative_path=relative_path,
        symbol=symbol,
        commits=tuple(commits_list),
        partial_reasons=tuple(partial_reasons) if partial_reasons else (),
    )
