from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from memoryforge.errors import ImportSafetyError
from memoryforge.importer import SourceImporter
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import Sensitivity, SourceCategory
from memoryforge.workspace import RAW_CATEGORIES, WIKI_DIRECTORIES, Workspace


def test_initialize_creates_the_specified_trusted_storage_layout(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "demo")

    assert workspace.config_path.is_file()
    assert workspace.schema_path.is_file()
    assert (workspace.root / ".git").is_dir()
    assert (workspace.wiki_dir / "INDEX.md").read_text(encoding="utf-8") == "# Knowledge Index\n"
    assert all((workspace.raw_dir / category).is_dir() for category in RAW_CATEGORIES)
    assert all((workspace.wiki_dir / page_type).is_dir() for page_type in WIKI_DIRECTORIES)
    assert workspace.index_path.is_file()
    assert len(workspace.current_commit()) == 40
    assert _git(workspace.root, "log", "-1", "--format=%s") == (
        "chore: initialize MemoryForge workspace"
    )
    assert _git(workspace.root, "status", "--porcelain") == ""
    assert set(_git(workspace.root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()) == {
        ".memoryforge/.gitignore",
        ".memoryforge/config.yaml",
        ".memoryforge/schema.yaml",
        ".memoryforgeignore",
        "AGENTS.md",
        "wiki/INDEX.md",
    }


def test_import_is_idempotent_and_indexes_an_immutable_source(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    input_file = tmp_path / "cache-design.md"
    input_file.write_text(
        "# Cache design\n\nUse a namespaced hash cache key for tenant isolation.\n",
        encoding="utf-8",
    )

    importer = SourceImporter(workspace)
    first = importer.import_file(
        input_file,
        category=SourceCategory.DESIGN,
        tags=("cache", "adr"),
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    repeated = importer.import_file(input_file, category=SourceCategory.DESIGN)

    raw_path = workspace.root / first.source.uri
    manifests = SourceManifestStore(workspace.manifest_dir).list_all()
    assert first.duplicate is False
    assert repeated.duplicate is True
    assert repeated.source == first.source
    assert raw_path.read_text(encoding="utf-8") == input_file.read_text(encoding="utf-8")
    assert not raw_path.stat().st_mode & stat.S_IWUSR
    assert manifests == [first.source]
    assert workspace.source_index.search("namespaced") == [first.source.source_id]


def test_import_rejects_sensitive_and_unsupported_sources(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    dotenv = tmp_path / ".env"
    dotenv.write_text("TOKEN=not-a-real-token\n", encoding="utf-8")
    binary_database = tmp_path / "state.sqlite"
    binary_database.write_bytes(b"SQLite format 3\x00")
    importer = SourceImporter(workspace)

    with pytest.raises(ImportSafetyError, match="sensitive"):
        importer.import_file(dotenv, category=SourceCategory.NOTES)
    with pytest.raises(ImportSafetyError, match="binary database"):
        importer.import_file(binary_database, category=SourceCategory.NOTES)


def test_import_respects_workspace_ignore_rules(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    ignored_source = tmp_path / "private-design.md"
    ignored_source.write_text("# Draft\n", encoding="utf-8")
    (workspace.root / ".memoryforgeignore").write_text(
        "*.private.md\nprivate-*.md\n",
        encoding="utf-8",
    )

    with pytest.raises(ImportSafetyError, match="excluded"):
        SourceImporter(workspace).import_file(ignored_source, category=SourceCategory.DESIGN)


def _git(workspace_root: Path, *arguments: str) -> str:
    """Run an assertion-oriented Git command inside a generated workspace."""

    completed = subprocess.run(
        ["git", "-C", str(workspace_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
