"""Small Git wrapper for MemoryForge's versioned stable knowledge layer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from memoryforge.errors import WorkspaceError

BASELINE_COMMIT_MESSAGE = "chore: initialize MemoryForge workspace"
FALLBACK_AUTHOR_NAME = "MemoryForge"
FALLBACK_AUTHOR_EMAIL = "memoryforge@localhost"


class GitVersionStore:
    """Initializes a dedicated workspace repository and exposes its current base."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def initialize(self) -> None:
        """Create an empty Git repository with `main` as its initial branch."""

        if (self.root / ".git").exists():
            raise WorkspaceError(f"Refusing to reuse existing Git repository: {self.root}")
        self._run(["init", "--quiet", "--initial-branch=main"], check=True)

    def ensure_baseline(self, paths: tuple[str, ...]) -> str:
        """Commit the initial workspace contract and return the resulting SHA."""

        existing = self.head()
        if existing is not None:
            return existing

        self._run(["add", "--", *paths], check=True)
        extra_config = self._commit_identity()
        self._run(
            ["commit", "--quiet", "-m", BASELINE_COMMIT_MESSAGE],
            check=True,
            extra_config=extra_config,
        )
        baseline = self.head()
        if baseline is None:
            raise WorkspaceError("Git baseline commit completed without creating HEAD")
        return baseline

    def head(self) -> Optional[str]:
        """Return the current commit SHA, or `None` before the first commit."""

        completed = self._run(["rev-parse", "--verify", "HEAD"], check=False)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def paths_are_clean(self, paths: tuple[str, ...]) -> bool:
        """Return whether selected paths have no tracked or untracked changes."""

        completed = self._run(
            ["status", "--porcelain", "--untracked-files=all", "--", *paths],
            check=True,
        )
        return not completed.stdout.strip()

    def commit(self, paths: tuple[str, ...], message: str) -> str:
        """Commit only the supplied workspace paths and return the new revision."""

        if not paths:
            raise WorkspaceError("Cannot create a MemoryForge commit without paths")
        self._run(["add", "--", *paths], check=True)
        staged = self._run(["diff", "--cached", "--quiet"], check=False)
        if staged.returncode == 0:
            raise WorkspaceError("No staged MemoryForge changes to commit")
        if staged.returncode != 1:
            raise WorkspaceError("Unable to inspect staged MemoryForge changes")
        self._run(
            ["commit", "--quiet", "-m", message],
            check=True,
            extra_config=self._commit_identity(),
        )
        commit = self.head()
        if commit is None:
            raise WorkspaceError("MemoryForge commit completed without creating HEAD")
        return commit

    def unstage(self, paths: tuple[str, ...]) -> None:
        """Remove selected paths from the index without changing working files."""

        if paths:
            self._run(["reset", "--quiet", "HEAD", "--", *paths], check=True)

    def _commit_identity(self) -> tuple[str, ...]:
        """Use repository/user configuration when available, with a local fallback."""

        name = self._config_value("user.name")
        email = self._config_value("user.email")
        if name and email:
            return ()
        return (
            "user.name=" + FALLBACK_AUTHOR_NAME,
            "user.email=" + FALLBACK_AUTHOR_EMAIL,
        )

    def _config_value(self, key: str) -> Optional[str]:
        completed = self._run(["config", "--get", key], check=False)
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return value or None

    def _run(
        self,
        arguments: list[str],
        check: bool,
        extra_config: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run Git inside the workspace and normalize failures for the caller."""

        command = ["git"]
        for value in extra_config:
            command.extend(["-c", value])
        command.extend(["-C", str(self.root), *arguments])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise WorkspaceError(f"Git command failed: {detail}")
        return completed
