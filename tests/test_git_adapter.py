from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memoryforge.adapters.git_adapter import (
    GitRepositoryError,
    scan_git_documentation,
    snapshot_git_repository,
)
from memoryforge.core.models import Sensitivity, SourceCategory


def test_scan_git_documentation_selects_tracked_documentation(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nOverview")
    _write(checkout / "CHANGELOG.txt", "Initial release")
    _write(checkout / "docs" / "architecture.md", "# Architecture\n\nWorkers")
    _write(checkout / "adr" / "001-storage.txt", "Use SQLite")
    _write(checkout / "src" / "main.py", "print('not documentation')")
    _commit_all(checkout)

    documents = scan_git_documentation(checkout)

    assert [document.source_path for document in documents] == [
        "CHANGELOG.txt",
        "README.md",
        "adr/001-storage.txt",
        "docs/architecture.md",
    ]
    assert [document.title for document in documents] == [
        "CHANGELOG",
        "README",
        "001-storage",
        "architecture",
    ]
    assert documents[0].content == "Initial release"
    assert documents[1].media_type == "text/markdown"
    assert documents[0].category is SourceCategory.REFS
    assert all(document.sensitivity is Sensitivity.LOCAL_ONLY for document in documents)


def test_git_snapshot_and_document_uris_are_stable_across_scans(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README", "Overview")
    _commit_all(checkout)

    first_snapshot = snapshot_git_repository(checkout)
    first_documents = scan_git_documentation(checkout)
    second_snapshot = snapshot_git_repository(checkout)
    second_documents = scan_git_documentation(checkout)

    assert first_snapshot == second_snapshot
    assert first_snapshot.revision
    assert first_documents == second_documents
    assert first_documents[0].source_uri.endswith("/README")
    assert first_documents[0].media_type == "text/plain"
    assert first_documents[0].suffix == ".txt"


def test_remote_url_becomes_repository_identity_in_source_uri(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(checkout, "remote", "add", "origin", "ssh://git@code.example.com/team/service.git")

    document = scan_git_documentation(checkout)[0]

    assert "ssh%3A%2F%2Fcode.example.com%2Fteam%2Fservice.git" in document.source_uri
    assert "git%40" not in document.source_uri
    assert document.source_uri.endswith("/README.md")


def test_scan_git_documentation_rejects_non_git_path(tmp_path: Path) -> None:
    checkout = tmp_path / "not-a-repository"
    checkout.mkdir()

    with pytest.raises(GitRepositoryError, match="not a Git checkout"):
        scan_git_documentation(checkout)


def test_scan_git_documentation_reads_head_not_dirty_worktree(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "Committed overview")
    _commit_all(checkout)
    _write(checkout / "README.md", "Uncommitted change")

    document = scan_git_documentation(checkout)[0]

    assert document.content == "Committed overview"


def test_scan_git_documentation_skips_head_symlinks(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    external_secret = tmp_path / "outside.md"
    external_secret.write_text("outside checkout", encoding="utf-8")
    (checkout / "docs").mkdir()
    (checkout / "docs" / "linked.md").symlink_to(external_secret)
    _write(checkout / "docs" / "kept.md", "Inside checkout")
    _commit_all(checkout)

    documents = scan_git_documentation(checkout)

    assert [document.source_path for document in documents] == ["docs/kept.md"]
    assert all("outside checkout" not in document.content for document in documents)


def test_scan_git_documentation_skips_non_text_and_binary_documents(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "docs" / "kept.md", "Text documentation")
    _write_bytes(checkout / "docs" / "image.png", b"\x89PNG\r\n\x1a\n")
    _write_bytes(checkout / "docs" / "binary.txt", b"\x00\xff\x10")
    _write(checkout / "README.rst", "Not included")
    _commit_all(checkout)

    documents = scan_git_documentation(checkout)

    assert [document.source_path for document in documents] == ["docs/kept.md"]


def test_remote_url_credentials_are_not_persisted_in_source_uri(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "https://build-user:secret-token@code.example.com/team/service.git",
    )

    document = scan_git_documentation(checkout)[0]

    assert "build-user" not in document.source_uri
    assert "secret-token" not in document.source_uri
    assert "https%3A%2F%2Fcode.example.com%2Fteam%2Fservice.git" in document.source_uri


def test_malformed_remote_port_falls_back_to_local_repository_identity(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "https://user:token@code.example.com:bad/service.git",
    )

    document = scan_git_documentation(checkout)[0]

    assert "user" not in document.source_uri
    assert "token" not in document.source_uri
    assert str(checkout.resolve()).replace("/", "%2F") in document.source_uri


def test_remote_query_and_fragment_are_not_persisted_in_source_uri(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "https://code.example.com/team/service.git?access_token=query-token#secret-fragment",
    )

    document = scan_git_documentation(checkout)[0]

    assert "query-token" not in document.source_uri
    assert "secret-fragment" not in document.source_uri
    assert "https%3A%2F%2Fcode.example.com%2Fteam%2Fservice.git" in document.source_uri


def test_scp_remote_credentials_and_suffixes_are_not_persisted(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "git@code.example.com:team/service.git?access_token=scp-token",
    )

    document = scan_git_documentation(checkout)[0]

    assert "git%40" not in document.source_uri
    assert "scp-token" not in document.source_uri
    assert "code.example.com%3Ateam%2Fservice.git" in document.source_uri


def test_malformed_scp_remote_falls_back_to_local_repository_identity(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service")
    _commit_all(checkout)
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "git@code.example.com?token=secret:team/service.git",
    )

    document = scan_git_documentation(checkout)[0]

    assert "secret" not in document.source_uri
    assert str(checkout.resolve()).replace("/", "%2F") in document.source_uri


def _create_repository(tmp_path: Path) -> Path:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    return checkout


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _commit_all(checkout: Path) -> None:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Initial documentation")


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=checkout, check=True, capture_output=True, text=True)
