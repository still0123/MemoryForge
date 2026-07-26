from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

import memoryforge.importer as importer_module
import memoryforge.workspace as workspace_module
from memoryforge.importer import (
    MAX_SOURCE_BYTES,
    SourceValidationError,
    import_local_file,
    validate_source_path,
)
from memoryforge.workspace import (
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    init_workspace,
)


@pytest.mark.parametrize("name", ["design.md", "notes.markdown", "summary.txt"])
def test_validate_source_path_accepts_supported_text_files(
    tmp_path: Path,
    name: str,
) -> None:
    source = tmp_path / name
    source.write_text("safe public content", encoding="utf-8")

    assert validate_source_path(source, source_root=tmp_path) == source.resolve()


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "api_key.txt",
        "credentials.txt",
        "password.md",
        "secret.md",
        "token.txt",
        "id_rsa",
    ],
)
def test_validate_source_path_rejects_sensitive_names(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.write_text("must not be imported", encoding="utf-8")

    with pytest.raises(SourceValidationError, match="sensitive file name"):
        validate_source_path(source, source_root=tmp_path)


def test_validate_source_path_rejects_unsupported_file_type(tmp_path: Path) -> None:
    source = tmp_path / "architecture.pdf"
    source.write_bytes(b"%PDF-public-placeholder")

    with pytest.raises(SourceValidationError, match="only .md"):
        validate_source_path(source, source_root=tmp_path)


def test_validate_source_path_rejects_source_outside_allowed_root(tmp_path: Path) -> None:
    allowed_root = tmp_path / "repository"
    allowed_root.mkdir()
    private_source = tmp_path / "private-notes.md"
    private_source.write_text("private", encoding="utf-8")

    with pytest.raises(SourceValidationError, match="inside the allowed root"):
        validate_source_path(private_source, source_root=allowed_root)


def test_validate_source_path_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "linked.md"
    link.symlink_to(target)

    with pytest.raises(SourceValidationError, match="symbolic links"):
        validate_source_path(link, source_root=tmp_path)


def test_validate_source_path_rejects_file_over_size_limit(tmp_path: Path) -> None:
    source = tmp_path / "oversized.txt"
    source.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))

    with pytest.raises(SourceValidationError, match="5 MiB"):
        validate_source_path(source, source_root=tmp_path)


def test_import_local_file_is_idempotent_and_preserves_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "design.md"
    content = "# Cache design\n\nUse a versioned namespace."
    source.write_text(content, encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    first = import_local_file(
        workspace,
        source,
        category="design",
        source_root=source_root,
    )
    second = import_local_file(
        workspace,
        source,
        category="design",
        source_root=source_root,
    )

    assert first.status == "created"
    assert second.status == "unchanged"
    assert first.content_sha256 == second.content_sha256
    assert first.title == "Cache design"
    assert first.source_uri == f"mf://source/{first.source_id}"
    assert first.snapshot_uri == f"mf://blob/{first.content_sha256}"
    assert first.snapshot_path.endswith(f"{first.content_sha256}.blob")
    assert (workspace / first.snapshot_path).read_text(encoding="utf-8") == content
    assert len(list((workspace / "raw").rglob("*.blob"))) == 1
    assert stat.S_IMODE((workspace / first.snapshot_path).stat().st_mode) == 0o600
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (1,)


def test_import_local_file_preserves_distinct_sources_while_reusing_blob(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    first_source = source_root / "first.md"
    second_source = source_root / "second.md"
    content = "# Shared design\n\nIdentical content."
    first_source.write_text(content, encoding="utf-8")
    second_source.write_text(content, encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    first = import_local_file(workspace, first_source, source_root=source_root)
    second = import_local_file(workspace, second_source, source_root=source_root)

    assert first.status == "created"
    assert second.status == "created"
    assert first.source_id != second.source_id
    assert first.content_sha256 == second.content_sha256
    assert first.snapshot_path == second.snapshot_path
    assert len(list((workspace / "raw").rglob("*.blob"))) == 1
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (1,)


def test_import_local_file_concurrently_is_idempotent(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "design.md"
    source.write_text("# Concurrent design\n\nOne immutable blob.", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: import_local_file(workspace, source, source_root=source_root),
                range(8),
            )
        )

    assert sum(result.status == "created" for result in results) == 1
    assert sum(result.status == "unchanged" for result in results) == 7
    assert len({result.snapshot_path for result in results}) == 1
    assert len(list((workspace / "raw").rglob("*.blob"))) == 1
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (1,)


def test_import_same_content_from_different_uris_concurrently_reuses_blob(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    sources = [source_root / "first.md", source_root / "second.md"]
    for source in sources:
        source.write_text("# Shared\n\nSame bytes, distinct provenance.", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda source: import_local_file(workspace, source, source_root=source_root),
                sources,
            )
        )

    assert [result.status for result in results] == ["created", "created"]
    assert len({result.source_id for result in results}) == 2
    assert len({result.snapshot_path for result in results}) == 1
    assert len(list((workspace / "raw").rglob("*.blob"))) == 1
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (1,)


@pytest.mark.parametrize(
    "content",
    [
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----",
        "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF",
        "API_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyzAB",
        "PASSWORD=correct-horse-battery-staple",
        "AIzaSyA1234567890abcdefghijklmnopqrst",
        "sk_" + "live_51M3VeryLongSyntheticTokenValue",
        "sk_" + "test_51M3VeryLongSyntheticTokenValue",
        "glpat-AbCdEfGhIjKlMnOpQrSt",
        "MY_SERVICE_API_KEY=super-secret-value-12345",
        "DATABASE_PASSWORD=correct-horse-battery-staple",
        "DEPLOY_TOKEN=synthetic-token-value-12345",
        "PAYMENT_SECRET=synthetic-secret-value-12345",
        "AWS_SECRET_ACCESS_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789ABCD",
        "npm_AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "sk_" + "live_AbCdEftestGhIjKlMnOpQrStUvWxYz012345",
    ],
)
def test_import_local_file_rejects_high_confidence_secret_content(
    tmp_path: Path,
    content: str,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "notes.md"
    source.write_text(content, encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    with pytest.raises(SourceValidationError) as exc_info:
        import_local_file(workspace, source, source_root=source_root)

    assert "high-confidence secret pattern" in str(exc_info.value)
    assert content not in str(exc_info.value)


@pytest.mark.parametrize(
    "placeholder",
    [
        "API_KEY=your_api_key_here",
        "sk_test_exampleplaceholdervalue",
        "AIzaExamplePlaceholderValue1234567890",
        "glpat-exampleplaceholdervalue",
    ],
)
def test_import_local_file_accepts_secret_documentation_placeholders(
    tmp_path: Path,
    placeholder: str,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "configuration-guide.md"
    source.write_text(
        f"# Configuration\n\nUse `{placeholder}` only as documentation.",
        encoding="utf-8",
    )
    workspace = init_workspace(tmp_path / "workspace")

    result = import_local_file(workspace, source, source_root=source_root)

    assert result.status == "created"


def test_import_rejects_raw_symlink_escape_after_initialization(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "notes.md"
    source.write_text("# Safe note", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "raw").rmdir()
    (workspace / "raw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        import_local_file(workspace, source, source_root=source_root)

    assert list(outside.iterdir()) == []


def test_import_local_file_rejects_invalid_category_and_non_utf8(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "notes.txt"
    source.write_bytes(b"\xff\xfe")
    workspace = init_workspace(tmp_path / "workspace")

    with pytest.raises(SourceValidationError, match="category"):
        import_local_file(
            workspace,
            source,
            category="../private",
            source_root=source_root,
        )
    with pytest.raises(SourceValidationError, match="valid UTF-8"):
        import_local_file(workspace, source, source_root=source_root)


def test_import_local_file_rejects_tampered_snapshot_before_unchanged(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n\ntrusted content", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    imported = import_local_file(workspace, source, source_root=source_root)
    (workspace / imported.snapshot_path).write_text("tampered content", encoding="utf-8")

    with pytest.raises(WorkspaceIntegrityError, match="integrity"):
        import_local_file(workspace, source, source_root=source_root)


def test_import_local_file_category_change_creates_new_version(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "design.md"
    source.write_text("# Design\n\nstable content", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    first = import_local_file(
        workspace,
        source,
        category="design",
        source_root=source_root,
    )
    second = import_local_file(
        workspace,
        source,
        category="postmortem",
        source_root=source_root,
    )

    assert first.status == "created"
    assert second.status == "updated"
    assert second.source_id == first.source_id
    assert second.content_sha256 == first.content_sha256
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (2,)
        assert connection.execute(
            "SELECT category FROM source_versions WHERE is_current = 1"
        ).fetchone() == ("postmortem",)


def test_import_local_file_persists_only_safe_relative_paths(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    nested = source_root / "docs"
    nested.mkdir()
    source = nested / "design.md"
    source.write_text("# Design\n\nportable provenance", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    result = import_local_file(workspace, source, source_root=source_root)
    serialized = result.model_dump_json()

    assert str(tmp_path) not in serialized
    assert result.source_uri == f"mf://source/{result.source_id}"
    assert result.snapshot_uri == f"mf://blob/{result.content_sha256}"
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        source_row = connection.execute("SELECT source_uri, source_path FROM sources").fetchone()
        blob_row = connection.execute("SELECT snapshot_path FROM blobs").fetchone()
    assert source_row == (result.source_uri, "docs/design.md")
    assert blob_row == (result.snapshot_path,)
    assert str(tmp_path) not in repr((source_row, blob_row))


def test_source_id_is_full_sha256_of_relative_path_and_stable_across_roots(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "moved-root"
    first_root.mkdir()
    second_root.mkdir()
    relative = Path("docs/design.md")
    (first_root / relative.parent).mkdir()
    (second_root / relative.parent).mkdir()
    (first_root / relative).write_text("# Design\n\nfirst observation", encoding="utf-8")
    (second_root / relative).write_text("# Design\n\nsecond observation", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    first = import_local_file(workspace, first_root / relative, source_root=first_root)
    second = import_local_file(workspace, second_root / relative, source_root=second_root)

    expected = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
    assert first.source_id == expected
    assert second.source_id == expected
    assert len(expected) == 64
    assert second.status == "updated"
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem alias behavior")
def test_macos_case_alias_uses_filesystem_canonical_source_identity(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    canonical = source_root / "Design.md"
    alias = source_root / "design.md"
    canonical.write_text("# Design\n\ncase alias", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    if alias.exists() and os.path.samefile(canonical, alias):
        first = import_local_file(workspace, canonical, source_root=source_root)
        second = import_local_file(workspace, alias, source_root=source_root)

        assert second.status == "unchanged"
        assert second.source_id == first.source_id
        with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (1,)
    else:
        alias.write_text("# Design\n\ndistinct case-sensitive file", encoding="utf-8")
        first = import_local_file(workspace, canonical, source_root=source_root)
        second = import_local_file(workspace, alias, source_root=source_root)

        assert first.source_id != second.source_id
        with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (2,)


def test_unicode_normalization_aliases_follow_filesystem_identity(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    nfc_name = "café.md"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    nfc_source = source_root / nfc_name
    nfd_alias = source_root / nfd_name
    nfc_source.write_text("# Unicode\n\nnormalization alias", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    if nfd_alias.exists() and os.path.samefile(nfc_source, nfd_alias):
        first = import_local_file(workspace, nfc_source, source_root=source_root)
        second = import_local_file(workspace, nfd_alias, source_root=source_root)

        assert second.status == "unchanged"
        assert second.source_id == first.source_id
        with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
            paths = connection.execute("SELECT source_path FROM sources").fetchall()
        assert paths == [(nfc_name,)]
    else:
        nfd_alias.write_text("# Unicode\n\ndistinct normalization file", encoding="utf-8")
        assert not os.path.samefile(nfc_source, nfd_alias)
        first = import_local_file(workspace, nfc_source, source_root=source_root)
        second = import_local_file(workspace, nfd_alias, source_root=source_root)

        assert second.status == "created"
        assert second.source_id != first.source_id
        with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
            paths = connection.execute(
                "SELECT source_path FROM sources ORDER BY source_path"
            ).fetchall()
        assert set(paths) == {(nfc_name,), (nfd_name,)}


def test_import_rejects_leaf_symlink_replacement_during_secure_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "notes.md"
    source.write_text("# Safe note", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Private outside note", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    actual_open = importer_module.os.open
    attacked = False

    def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal attacked
        if not attacked and path == "notes.md" and dir_fd is not None:
            attacked = True
            source.unlink()
            source.symlink_to(outside)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(importer_module.os, "open", replacing_open)

    with pytest.raises(SourceValidationError, match="opened safely"):
        import_local_file(workspace, source, source_root=source_root)

    assert attacked


def test_import_rejects_intermediate_directory_replacement_during_secure_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "repository"
    docs = source_root / "docs"
    docs.mkdir(parents=True)
    source = docs / "notes.md"
    source.write_text("# Safe note", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("# Private outside note", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    actual_open = importer_module.os.open
    attacked = False

    def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal attacked
        if not attacked and path == "docs" and dir_fd is not None:
            attacked = True
            docs.rename(source_root / "docs-original")
            docs.symlink_to(outside, target_is_directory=True)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(importer_module.os, "open", replacing_open)

    with pytest.raises(SourceValidationError, match="opened safely"):
        import_local_file(workspace, source, source_root=source_root)

    assert attacked


def test_import_local_file_rejects_blob_directory_replacement_attack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    content = "# Evidence\n\nsecure write"
    source.write_text(content, encoding="utf-8")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    workspace = init_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    actual_open = workspace_module.os.open
    attacked = False

    def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal attacked
        path_name = Path(path).name
        if not attacked and path_name.startswith(content_sha256):
            attacked = True
            prefix = workspace / "raw" / "blobs" / content_sha256[:2]
            quarantine = workspace / "raw" / "blobs" / f"{content_sha256[:2]}-moved"
            prefix.rename(quarantine)
            prefix.symlink_to(outside, target_is_directory=True)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(workspace_module.os, "open", replacing_open)

    with pytest.raises(WorkspaceSecurityError, match="changed during blob"):
        import_local_file(workspace, source, source_root=source_root)

    assert attacked
    assert list(outside.iterdir()) == []


def test_import_local_file_safely_reuses_content_addressed_orphan_blob(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    content = "# Evidence\n\nrecoverable orphan"
    source.write_text(content, encoding="utf-8")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    workspace = init_workspace(tmp_path / "workspace")
    orphan_directory = workspace / "raw" / "blobs" / content_sha256[:2]
    orphan_directory.mkdir(parents=True)
    orphan = orphan_directory / f"{content_sha256}.blob"
    orphan.write_text(content, encoding="utf-8")

    result = import_local_file(workspace, source, source_root=source_root)

    assert result.status == "created"
    assert workspace / result.snapshot_path == orphan


def test_import_recovers_from_half_written_final_blob(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    content = "# Evidence\n\ncomplete atomic content"
    source.write_text(content, encoding="utf-8")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    workspace = init_workspace(tmp_path / "workspace")
    blob_directory = workspace / "raw" / "blobs" / content_sha256[:2]
    blob_directory.mkdir(parents=True)
    final_blob = blob_directory / f"{content_sha256}.blob"
    final_blob.write_bytes(content.encode()[:8])

    result = import_local_file(workspace, source, source_root=source_root)

    assert result.status == "created"
    assert final_blob.read_text(encoding="utf-8") == content
    assert stat.S_IMODE(final_blob.stat().st_mode) == 0o600


def test_import_cleans_stale_temp_for_same_hash(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    content = "# Evidence\n\nrecover stale temporary file"
    source.write_text(content, encoding="utf-8")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    workspace = init_workspace(tmp_path / "workspace")
    blob_directory = workspace / "raw" / "blobs" / content_sha256[:2]
    blob_directory.mkdir(parents=True)
    stale = blob_directory / f"{content_sha256}.tmp-deadbeef"
    stale.write_bytes(b"partial")

    result = import_local_file(workspace, source, source_root=source_root)

    assert result.status == "created"
    assert not stale.exists()
    assert list(blob_directory.glob(f"{content_sha256}.tmp-*")) == []

    stale_after_commit = blob_directory / f"{content_sha256}.tmp-abandoned"
    stale_after_commit.write_bytes(b"partial")
    unchanged = import_local_file(workspace, source, source_root=source_root)

    assert unchanged.status == "unchanged"
    assert not stale_after_commit.exists()
    assert len(list((workspace / "raw/blobs").rglob(f"{content_sha256}*"))) == 1
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (1,)
