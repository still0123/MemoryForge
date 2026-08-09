"""Small Git wrapper for the versioned stable knowledge layer."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath

from memoryforge.errors import WorkspaceError

BASELINE_COMMIT_MESSAGE = "chore: initialize MemoryForge workspace"
FALLBACK_AUTHOR_NAME = "MemoryForge"
FALLBACK_AUTHOR_EMAIL = "memoryforge@localhost"
_COMMIT_ID = re.compile(r"^[a-f0-9]{40,64}$")


class GitVersionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._created_repository = False

    def initialize(self) -> None:
        if self.has_repository():
            raise WorkspaceError("workspace already contains a Git repository")
        self._run(
            ["init", "--quiet", "--initial-branch=main"],
            check=True,
            allow_missing_repository=True,
        )
        self.validate_metadata()
        self._created_repository = True

    def ensure_baseline(self, paths: tuple[str, ...]) -> str:
        if not self._created_repository:
            raise WorkspaceError(
                "automatic Git baseline is only allowed for a newly created controlled repository"
            )
        if self.head() is not None:
            raise WorkspaceError("new controlled repository already has a Git HEAD")
        primary_index = self.root / ".git/index"
        index_descriptor, index_name = tempfile.mkstemp(
            prefix="memoryforge-index-",
            dir=self.root / ".git",
        )
        os.close(index_descriptor)
        os.unlink(index_name)
        isolated_index = Path(index_name)
        try:
            self._run(["add", "--", *paths], check=True, index_file=isolated_index)
            self._run(
                ["commit", "--quiet", "-m", BASELINE_COMMIT_MESSAGE],
                check=True,
                extra_config=self._commit_identity(),
                index_file=isolated_index,
            )
            os.replace(isolated_index, primary_index)
        finally:
            with suppress(FileNotFoundError):
                isolated_index.unlink()
        baseline = self.head()
        if baseline is None:
            raise WorkspaceError("Git baseline commit completed without creating HEAD")
        return baseline

    def has_repository(self) -> bool:
        try:
            os.lstat(self.root / ".git")
        except FileNotFoundError:
            return False
        return True

    def validate_metadata(self, *, allow_missing: bool = False) -> None:
        git_entry = self.root / ".git"
        try:
            metadata = os.lstat(git_entry)
        except FileNotFoundError:
            if allow_missing:
                return
            raise WorkspaceError("workspace is missing its Git repository") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise WorkspaceError("workspace Git metadata must not be a symbolic link")
        if not stat.S_ISDIR(metadata.st_mode):
            raise WorkspaceError("workspace Git metadata must be a real directory")
        for relative in ("objects", "refs"):
            path = git_entry / relative
            try:
                child = os.lstat(path)
            except FileNotFoundError:
                raise WorkspaceError(
                    f"workspace Git metadata is missing its {relative} directory"
                ) from None
            if stat.S_ISLNK(child.st_mode) or not stat.S_ISDIR(child.st_mode):
                raise WorkspaceError(f"workspace Git {relative} metadata must be a real directory")
            self._validate_metadata_tree(path)
        for relative in ("HEAD", "config", "index"):
            path = git_entry / relative
            try:
                child = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(child.st_mode) or not stat.S_ISREG(child.st_mode):
                raise WorkspaceError(f"workspace Git {relative} metadata must be a regular file")

    def _validate_metadata_tree(self, root: Path) -> None:
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise WorkspaceError(
                    "workspace Git metadata could not be inspected safely"
                ) from exc
            for entry in entries:
                if entry.is_symlink():
                    raise WorkspaceError(
                        f"workspace Git metadata must not contain symbolic links: {entry.path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif not entry.is_file(follow_symlinks=False):
                    raise WorkspaceError(
                        f"workspace Git metadata entry must be regular: {entry.path}"
                    )

    def head(self) -> str | None:
        completed = self._run(["rev-parse", "--verify", "HEAD"], check=False)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def tracks(self, paths: tuple[str, ...]) -> bool:
        completed = self._run(["ls-tree", "-r", "--name-only", "HEAD"], check=False)
        if completed.returncode != 0:
            return False
        tracked = set(completed.stdout.splitlines())
        return all(path in tracked for path in paths)

    def read_text_at(self, commit: str, path: str) -> str | None:
        """Read one stable Wiki file at a fixed Commit without changing the worktree."""
        parts = PurePosixPath(path).parts
        if (
            not parts
            or parts[0] != "wiki"
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in path
            or str(PurePosixPath(path)) != path
        ):
            raise WorkspaceError("invalid historical Wiki file identity")
        self._require_commit(commit)
        if self._run(["cat-file", "-e", f"{commit}:{path}"], check=False).returncode != 0:
            return None
        completed = self._run(["show", f"{commit}:{path}"], check=True)
        return completed.stdout

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Return whether two validated Commits form the expected history chain."""
        self._require_commit(ancestor)
        self._require_commit(descendant)
        return (
            self._run(
                ["merge-base", "--is-ancestor", ancestor, descendant],
                check=False,
            ).returncode
            == 0
        )

    def commit_paths(self, paths: tuple[str, ...], message: str) -> str:
        """Commit only the stable Wiki paths produced by one approved ChangeSet."""
        if not paths:
            raise WorkspaceError("cannot create an empty knowledge commit")
        self._run(["add", "--", *paths], check=True)
        self._run(
            ["commit", "--quiet", "--only", "-m", message, "--", *paths],
            check=True,
            extra_config=self._commit_identity(),
        )
        commit = self.head()
        if commit is None:
            raise WorkspaceError("knowledge commit completed without creating HEAD")
        return commit

    def require_clean_paths(self, paths: tuple[str, ...]) -> None:
        completed = self._run(
            ["status", "--porcelain", "--untracked-files=all", "--", *paths],
            check=True,
        )
        if completed.stdout.strip():
            raise WorkspaceError("refusing to apply over uncommitted changes in target Wiki paths")

    def reset_paths(self, paths: tuple[str, ...]) -> None:
        self._run(["reset", "--quiet", "HEAD", "--", *paths], check=True)

    def _commit_identity(self) -> tuple[str, ...]:
        name = self._config_value("user.name")
        email = self._config_value("user.email")
        if name and email:
            return ()
        return (
            "user.name=" + FALLBACK_AUTHOR_NAME,
            "user.email=" + FALLBACK_AUTHOR_EMAIL,
        )

    def _config_value(self, key: str) -> str | None:
        completed = self._run(["config", "--get", key], check=False)
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return value or None

    def _require_commit(self, commit: str) -> None:
        if (
            _COMMIT_ID.fullmatch(commit) is None
            or self._run(["cat-file", "-e", f"{commit}^{{commit}}"], check=False).returncode != 0
        ):
            raise WorkspaceError("historical Wiki Commit does not exist")

    def _run(
        self,
        arguments: list[str],
        *,
        check: bool,
        extra_config: tuple[str, ...] = (),
        allow_missing_repository: bool = False,
        index_file: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.validate_metadata(allow_missing=allow_missing_repository)
        command = ["git"]
        for value in extra_config:
            command.extend(["-c", value])
        command.extend(
            [
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgSign=false",
            ]
        )
        command.extend(["-C", str(self.root), *arguments])
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        environment["GIT_DIR"] = str(self.root / ".git")
        environment["GIT_WORK_TREE"] = str(self.root)
        environment["GIT_INDEX_FILE"] = str(
            index_file if index_file is not None else self.root / ".git/index"
        )
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = "/dev/null"
        source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
        if source_date_epoch.isdigit():
            git_date = f"@{int(source_date_epoch)} +0000"
            environment["GIT_AUTHOR_DATE"] = git_date
            environment["GIT_COMMITTER_DATE"] = git_date
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise WorkspaceError(f"Git command failed: {detail}")
        self.validate_metadata()
        return completed
