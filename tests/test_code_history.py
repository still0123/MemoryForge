from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from memoryforge.code_history import why_changed


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


def _init_repo(tmp: Path) -> tuple[Path, str]:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo, ""


def _commit_file(repo: Path, filename: str, content: str, msg: str) -> str:
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", msg)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_why_changed_returns_commits(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    sha1 = _commit_file(repo, "src/module.py", "def a():\n    pass\n", "initial module")
    sha2 = _commit_file(repo, "src/module.py", "def a():\n    return 1\n", "tweak a")
    result = why_changed(
        repo, commit_sha=sha2, relative_path="src/module.py", max_commits=5,
    )
    assert result.status == "answered"
    assert len(result.commits) >= 2
    shas = [c["sha"] for c in result.commits]
    assert sha2 == shas[0]
    assert sha1 in shas


def test_why_changed_commit_unreachable(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    _commit_file(repo, "x.py", "print(1)\n", "init")
    fake_sha = "f" * 40
    result = why_changed(
        repo, commit_sha=fake_sha, relative_path="x.py", max_commits=5,
    )
    assert "commit_unreachable" in result.partial_reasons


def test_why_changed_symbol_preserved(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    sha = _commit_file(repo, "src/m.py", "def hello():\n    pass\n", "add hello")
    result = why_changed(
        repo, commit_sha=sha, relative_path="src/m.py", symbol="m.hello", max_commits=5,
    )
    assert result.symbol == "m.hello"
    assert result.relative_path == "src/m.py"
