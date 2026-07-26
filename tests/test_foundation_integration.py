from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.importer import import_local_file
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import ChangeSet, ChangeSetStatus, Claim, ClaimStatus, Sensitivity
from memoryforge.workspace import Workspace


def test_workspace_has_clean_git_baseline_and_tracked_contract(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")

    assert len(workspace.current_commit()) == 40
    assert _git(workspace.root, "log", "-1", "--format=%s") == (
        "chore: initialize MemoryForge workspace"
    )
    assert _git(workspace.root, "status", "--porcelain") == ""
    assert set(_git(workspace.root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()) == {
        ".gitignore",
        ".memoryforge/config.yaml",
        ".memoryforge/schema.yaml",
        ".memoryforgeignore",
        "AGENTS.md",
        "wiki/INDEX.md",
    }


def test_import_preserves_source_versions_in_database_and_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "design.md"
    source.write_text("# Design v1\n", encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "wiki")

    first = import_local_file(
        workspace.root,
        source,
        source_root=source_root,
        tags=("cache", " cache "),
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    source.write_text("# Design v2\n", encoding="utf-8")
    second = import_local_file(
        workspace.root,
        source,
        source_root=source_root,
        tags=("cache",),
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    repeated = import_local_file(
        workspace.root,
        source,
        source_root=source_root,
        tags=("cache",),
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    manifests = SourceManifestStore(workspace.manifest_dir).list_all()

    assert first.status == "created"
    assert second.status == "updated"
    assert repeated.status == "unchanged"
    assert first.source_id == second.source_id
    assert len(manifests) == 2
    assert {manifest.content_sha256 for manifest in manifests} == {
        first.content_sha256,
        second.content_sha256,
    }
    assert all(manifest.sensitivity is Sensitivity.LOCAL_ONLY for manifest in manifests)
    assert all(manifest.tags == ("cache",) for manifest in manifests)


def test_cli_unifies_secure_import_search_and_future_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text("# Note\n\nSearchable evidence.\n", encoding="utf-8")
    workspace = repository / "wiki"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    imported = runner.invoke(
        app,
        [
            "import",
            str(source),
            "--workspace",
            str(workspace),
            "--local-only",
            "--tag",
            "evidence",
        ],
    )
    searched = runner.invoke(app, ["search", "Searchable", "--workspace", str(workspace)])
    unavailable = runner.invoke(app, ["ask", "current?", "--workspace", str(workspace)])

    assert imported.exit_code == 0
    assert json.loads(imported.stdout)["status"] == "created"
    assert searched.exit_code == 0
    assert json.loads(searched.stdout)[0]["source_path"] == "note.md"
    assert unavailable.exit_code == 2
    assert "not enabled" in unavailable.output
    manifests = SourceManifestStore(Workspace.open(workspace).manifest_dir).list_all()
    assert manifests[0].sensitivity is Sensitivity.LOCAL_ONLY
    assert manifests[0].tags == ("evidence",)


def test_claim_and_changeset_models_enforce_review_and_evidence() -> None:
    with pytest.raises(ValidationError, match="require at least one citation"):
        Claim(
            claim_id="clm_cache_key",
            subject="cache-key",
            predicate="uses",
            object="namespaced-hash",
            status=ClaimStatus.VERIFIED,
            confidence=0.96,
        )

    changeset = ChangeSet(
        changeset_id="chg_demo",
        base_commit="a" * 40,
        status=ChangeSetStatus.PROPOSED,
    )
    assert changeset.can_transition_to(ChangeSetStatus.VALIDATED)
    assert not changeset.can_transition_to(ChangeSetStatus.APPROVED)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
