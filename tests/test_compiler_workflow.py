from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.errors import WorkspaceError
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    Citation,
    Claim,
    ClaimStatus,
)
from memoryforge.version_store import GitVersionStore
from memoryforge.workspace import Workspace

SOURCE_TEXT = "# Cache policy\n\nCache entries expire after sixty seconds.\n"
MULTILINE_SOURCE_TEXT = (
    "# Distributed cache\n\n"
    "The cache uses namespaced keys\nand expires entries after sixty seconds.\n\n"
    "This paragraph is not the primary claim.\n"
)
CANDIDATE_PATH = "wiki/notes/cache-policy.md"
CANDIDATE_TEXT = (
    "# Cache policy\n\n## Verified facts\n\n- Cache entries expire after sixty seconds.\n"
)
ROUTED_CANDIDATE_PATH = "wiki/pages/cache-storage.md"


def test_ingest_pending_compiles_imported_source_to_proposed_changeset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changeset_id"].startswith("chg_")
    assert payload["status"] == "PROPOSED"
    assert payload["files"]
    assert all(path.startswith("wiki/") for path in payload["files"])
    assert any(path.startswith("wiki/pages/") for path in payload["files"])
    assert "claims" not in payload


def test_review_is_read_only_and_hides_legacy_claims(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, imported = _initialized_workspace(tmp_path, monkeypatch)
    changeset_id = _stage_changeset(workspace_path, imported)
    stable_wiki = workspace_path / CANDIDATE_PATH

    result = runner.invoke(
        app,
        ["review", changeset_id, "--workspace", str(workspace_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changeset_id"] == changeset_id
    assert payload["status"] == "PROPOSED"
    assert payload["candidate_files"][CANDIDATE_PATH] == CANDIDATE_TEXT
    assert payload["unified_diff"]
    assert CANDIDATE_PATH in payload["unified_diff"]
    assert "claims" not in payload
    assert not stable_wiki.exists()


def test_apply_requires_approval_then_writes_wiki_and_commits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, imported = _initialized_workspace(tmp_path, monkeypatch)
    changeset_id = _stage_changeset(workspace_path, imported)
    stable_wiki = workspace_path / CANDIDATE_PATH
    base_commit = _git_head(workspace_path)

    refused = runner.invoke(
        app,
        ["apply", changeset_id, "--workspace", str(workspace_path)],
    )

    assert refused.exit_code != 0
    assert not stable_wiki.exists()
    assert _git_head(workspace_path) == base_commit

    approved = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace_path)],
    )

    assert approved.exit_code == 0
    payload = json.loads(approved.stdout)
    assert payload["changeset_id"] == changeset_id
    assert payload["status"] == "APPLIED"
    assert stable_wiki.read_text(encoding="utf-8") == CANDIDATE_TEXT
    assert _git_head(workspace_path) != base_commit
    receipt_path = workspace_path / ".memoryforge/staging/applied" / changeset_id / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "APPLIED"
    assert receipt["commit"] == payload["commit"]
    assert receipt["changeset_id"] == changeset_id


def test_reject_archives_proposal_without_changing_stable_wiki(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, imported = _initialized_workspace(tmp_path, monkeypatch)
    changeset_id = _stage_changeset(workspace_path, imported)
    stable_wiki = workspace_path / CANDIDATE_PATH
    base_commit = _git_head(workspace_path)

    rejected = runner.invoke(
        app,
        ["reject", changeset_id, "--workspace", str(workspace_path)],
    )

    assert rejected.exit_code == 0, rejected.output
    assert json.loads(rejected.stdout) == {
        "changeset_id": changeset_id,
        "status": "REJECTED",
    }
    assert not stable_wiki.exists()
    assert _git_head(workspace_path) == base_commit
    assert not (workspace_path / ".memoryforge/staging" / changeset_id).exists()
    assert (workspace_path / ".memoryforge/staging/rejected" / changeset_id).is_dir()


def test_ingest_is_idempotent_until_apply_then_has_no_pending_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch)

    first = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    repeated = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])

    assert first.exit_code == 0
    assert repeated.exit_code == 0
    first_payload = json.loads(first.stdout)
    repeated_payload = json.loads(repeated.stdout)
    assert repeated_payload["changeset_id"] == first_payload["changeset_id"]

    applied = runner.invoke(
        app,
        [
            "apply",
            first_payload["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0

    pending = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert pending.exit_code == 0
    pending_payload = json.loads(pending.stdout)
    assert (
        pending_payload == []
        or pending_payload.get("pending") == []
        or (pending_payload.get("status") == "no_pending")
    )


def test_multiline_markdown_keeps_exact_citation_and_wiki_footnote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _initialized_workspace(
        tmp_path,
        monkeypatch,
        source_text=MULTILINE_SOURCE_TEXT,
    )

    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])

    assert ingested.exit_code == 0
    proposal = json.loads(ingested.stdout)
    assert "claims" not in proposal

    reviewed = runner.invoke(
        app,
        ["review", proposal["changeset_id"], "--workspace", str(workspace)],
    )
    assert reviewed.exit_code == 0
    review = json.loads(reviewed.stdout)
    candidate_path = next(path for path in proposal["files"] if path != "wiki/INDEX.md")
    candidate = review["candidate_files"][candidate_path]
    assert "source_version: 1" in candidate
    assert "The cache uses namespaced keys and expires entries after sixty seconds." in candidate
    assert "revision `1`" in candidate
    assert "`chars:21-92`" in candidate
    assert "mf://blob/" not in candidate
    assert "quote_sha256" not in candidate


def test_deterministic_compiler_keeps_multiple_citable_paragraphs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_text = (
        "# Cache policy\n\n"
        "Cache entries expire after sixty seconds.\n\n"
        "When the remote model is unavailable, the system falls back to local evidence.\n"
    )
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch, source_text=source_text)

    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    reviewed = runner.invoke(app, ["review", changeset_id, "--workspace", str(workspace)])

    assert reviewed.exit_code == 0, reviewed.output
    candidate_files = json.loads(reviewed.stdout)["candidate_files"]
    page = next(
        content for path, content in candidate_files.items() if path.startswith("wiki/pages/")
    )
    assert "Cache entries expire after sixty seconds." in page
    assert "system falls back to local evidence." in page
    assert page.count("[^source-") == 4


def test_deterministic_compiler_keeps_markdown_table_and_config_block_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_text = (
        "# Service configuration\n\n"
        "| Service | Address |\n"
        "| --- | --- |\n"
        "| Java Web | http://127.0.0.1:8766 |\n"
        "| Python AI | http://127.0.0.1:8765 |\n\n"
        "```bash\n"
        "export AD_VIDEO_LLM_ENABLED=1\n"
        "export AD_VIDEO_LLM_API_KEY=example-key\n"
        "```\n"
    )
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch, source_text=source_text)

    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    reviewed = runner.invoke(
        app,
        ["review", json.loads(ingested.stdout)["changeset_id"], "--workspace", str(workspace)],
    )

    assert reviewed.exit_code == 0, reviewed.output
    page = next(
        content
        for path, content in json.loads(reviewed.stdout)["candidate_files"].items()
        if path.startswith("wiki/pages/")
    )
    assert "http://127.0.0.1:8766" in page
    assert "http://127.0.0.1:8765" in page
    assert "AD_VIDEO_LLM_ENABLED=1" in page
    assert "AD_VIDEO_LLM_API_KEY=example-key" in page


def test_ingest_rejects_unknown_source_id(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch)
    unknown_source_id = "0" * 64

    result = runner.invoke(
        app,
        [
            "ingest",
            "--pending",
            "--source",
            unknown_source_id,
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code != 0
    assert "unknown source id" in (result.stdout + result.stderr).lower()
    assert unknown_source_id in (result.stdout + result.stderr)


def test_apply_rejects_manual_target_edit_and_preserves_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _initialized_workspace(tmp_path, monkeypatch)
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    proposal = json.loads(ingested.stdout)
    reviewed = runner.invoke(
        app,
        ["review", proposal["changeset_id"], "--workspace", str(workspace)],
    )
    target = workspace / proposal["files"][0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Manual edit\n", encoding="utf-8")

    applied = runner.invoke(
        app,
        [
            "apply",
            proposal["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )

    assert ingested.exit_code == 0
    assert reviewed.exit_code == 0
    assert applied.exit_code != 0
    assert target.read_text(encoding="utf-8") == "# Manual edit\n"


def test_apply_restores_old_file_and_keeps_proposal_when_git_commit_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, imported = _initialized_workspace(tmp_path, monkeypatch)
    initial = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace_path)],
    )
    assert initial.exit_code == 0, initial.output
    target_path = next(
        path for path in json.loads(initial.stdout)["files"] if path != "wiki/INDEX.md"
    )
    target = workspace_path / target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    old_content = "# Existing cache policy\n"
    target.write_text(old_content, encoding="utf-8")
    workspace = Workspace.open(workspace_path)
    workspace.version_store.commit_paths((target_path,), "test: add existing wiki page")
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace_path)],
    )
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    base_commit = _git_head(workspace_path)

    def fail_commit(
        _store: GitVersionStore,
        _paths: tuple[str, ...],
        _message: str,
    ) -> str:
        raise WorkspaceError("simulated commit failure")

    monkeypatch.setattr(GitVersionStore, "commit_paths", fail_commit)
    applied = runner.invoke(
        app,
        [
            "apply",
            changeset_id,
            "--approve",
            "--workspace",
            str(workspace_path),
        ],
    )

    assert ingested.exit_code == 0
    assert applied.exit_code != 0
    assert target.read_text(encoding="utf-8") == old_content
    assert _git_head(workspace_path) == base_commit
    staging = workspace_path / ".memoryforge/staging"
    assert (staging / changeset_id).is_dir()
    assert not (staging / "applied" / changeset_id).exists()
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        applied_version = connection.execute(
            "SELECT source_version_id FROM applied_source_versions WHERE source_id = ?",
            (imported["source_id"],),
        ).fetchone()
    assert applied_version is None


def test_apply_does_not_write_wiki_or_git_when_version_index_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, _ = _initialized_workspace(tmp_path, monkeypatch)
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace_path)],
    )
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    stored = ChangeSetStore(Workspace.open(workspace_path)).get(changeset_id)
    before = {
        path: (workspace_path / path).read_text(encoding="utf-8")
        if (workspace_path / path).is_file()
        else None
        for path in stored.candidate_files
    }
    base_commit = _git_head(workspace_path)

    def fail_version_index(
        _workspace: Workspace,
        _source_versions: dict[str, int],
    ) -> dict[str, int | None]:
        raise sqlite3.OperationalError("simulated version index failure")

    monkeypatch.setattr(Workspace, "record_applied_source_versions", fail_version_index)
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace_path)],
    )

    assert applied.exit_code != 0
    assert _git_head(workspace_path) == base_commit
    assert {
        path: (workspace_path / path).read_text(encoding="utf-8")
        if (workspace_path / path).is_file()
        else None
        for path in stored.candidate_files
    } == before
    staging = workspace_path / ".memoryforge/staging"
    assert (staging / changeset_id).is_dir()
    assert not (staging / "applied" / changeset_id).exists()


def test_apply_indexes_and_replaces_page_source_associations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, first = _initialized_workspace(tmp_path, monkeypatch)
    second = _import_extra_source(runner, workspace_path, "storage.md", "Storage is local.\n")
    third = _import_extra_source(runner, workspace_path, "queue.md", "Queue is durable.\n")

    first_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_first",
        source_ids=(first["source_id"], second["source_id"]),
        operation=ChangeOperationType.CREATE_PAGE,
    )
    first_apply = runner.invoke(
        app,
        ["apply", first_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert first_apply.exit_code == 0, first_apply.output
    opened = Workspace.open(workspace_path)
    assert opened.page_paths_for_source(first["source_id"]) == (ROUTED_CANDIDATE_PATH,)
    assert opened.page_paths_for_source(second["source_id"]) == (ROUTED_CANDIDATE_PATH,)

    replacement_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_replacement",
        source_ids=(second["source_id"], third["source_id"]),
        operation=ChangeOperationType.UPDATE_PAGE,
    )
    replacement_apply = runner.invoke(
        app,
        ["apply", replacement_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert replacement_apply.exit_code == 0, replacement_apply.output
    reopened = Workspace.open(workspace_path)
    assert reopened.page_paths_for_source(first["source_id"]) == ()
    assert reopened.page_paths_for_source(second["source_id"]) == (ROUTED_CANDIDATE_PATH,)
    assert reopened.page_paths_for_source(third["source_id"]) == (ROUTED_CANDIDATE_PATH,)


def test_pending_or_failed_apply_does_not_index_page_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, first = _initialized_workspace(tmp_path, monkeypatch)
    second = _import_extra_source(runner, workspace_path, "storage.md", "Storage is local.\n")
    changeset_id = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_failed",
        source_ids=(first["source_id"], second["source_id"]),
        operation=ChangeOperationType.CREATE_PAGE,
    )

    pending = runner.invoke(app, ["apply", changeset_id, "--workspace", str(workspace_path)])

    assert pending.exit_code != 0
    assert Workspace.open(workspace_path).page_paths_for_source(first["source_id"]) == ()

    def fail_commit(
        _store: GitVersionStore,
        _paths: tuple[str, ...],
        _message: str,
    ) -> str:
        raise WorkspaceError("simulated commit failure")

    monkeypatch.setattr(GitVersionStore, "commit_paths", fail_commit)
    failed = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace_path)],
    )

    assert failed.exit_code != 0
    assert Workspace.open(workspace_path).page_paths_for_source(first["source_id"]) == ()
    assert Workspace.open(workspace_path).page_paths_for_source(second["source_id"]) == ()


def test_apply_rejects_routed_page_without_sources_before_replacing_mappings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, first = _initialized_workspace(tmp_path, monkeypatch)
    second = _import_extra_source(runner, workspace_path, "storage.md", "Storage is local.\n")
    first_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_valid",
        source_ids=(first["source_id"], second["source_id"]),
        operation=ChangeOperationType.CREATE_PAGE,
    )
    applied = runner.invoke(
        app,
        ["apply", first_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert applied.exit_code == 0, applied.output
    stable_page = workspace_path / ROUTED_CANDIDATE_PATH
    stable_content = stable_page.read_text(encoding="utf-8")
    base_commit = _git_head(workspace_path)

    replacement_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_missing",
        source_ids=(first["source_id"], second["source_id"]),
        operation=ChangeOperationType.UPDATE_PAGE,
        include_sources=False,
    )
    failed = runner.invoke(
        app,
        ["apply", replacement_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert failed.exit_code != 0
    assert _git_head(workspace_path) == base_commit
    assert stable_page.read_text(encoding="utf-8") == stable_content
    opened = Workspace.open(workspace_path)
    assert opened.page_paths_for_source(first["source_id"]) == (ROUTED_CANDIDATE_PATH,)
    assert opened.page_paths_for_source(second["source_id"]) == (ROUTED_CANDIDATE_PATH,)


def test_apply_rejects_mismatched_page_sources_before_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, first = _initialized_workspace(tmp_path, monkeypatch)
    second = _import_extra_source(runner, workspace_path, "storage.md", "Storage is local.\n")
    source_ids = (first["source_id"], second["source_id"])
    initial_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_consistent",
        source_ids=source_ids,
        operation=ChangeOperationType.CREATE_PAGE,
        source_versions=_current_source_versions(workspace_path, source_ids),
    )
    initial_apply = runner.invoke(
        app,
        ["apply", initial_changeset, "--approve", "--workspace", str(workspace_path)],
    )
    assert initial_apply.exit_code == 0, initial_apply.output

    storage_path = workspace_path.parent / "storage.md"
    storage_path.write_text("Storage is replicated locally.\n", encoding="utf-8")
    updated = runner.invoke(
        app,
        ["import", str(storage_path), "--workspace", str(workspace_path), "--category", "notes"],
    )
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.stdout)["status"] == "updated"

    mismatched_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_page_sources_mismatched",
        source_ids=source_ids,
        operation=ChangeOperationType.UPDATE_PAGE,
        declared_source_ids=(first["source_id"],),
        source_versions=_current_source_versions(workspace_path, source_ids),
    )
    stable_page = workspace_path / ROUTED_CANDIDATE_PATH
    stable_content = stable_page.read_text(encoding="utf-8")
    base_commit = _git_head(workspace_path)
    before_source_versions = _applied_source_versions(workspace_path)
    before_page_sources = {
        source_id: Workspace.open(workspace_path).page_paths_for_source(source_id)
        for source_id in source_ids
    }

    failed = runner.invoke(
        app,
        ["apply", mismatched_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert failed.exit_code != 0
    assert _git_head(workspace_path) == base_commit
    assert stable_page.read_text(encoding="utf-8") == stable_content
    assert _applied_source_versions(workspace_path) == before_source_versions
    assert {
        source_id: Workspace.open(workspace_path).page_paths_for_source(source_id)
        for source_id in source_ids
    } == before_page_sources


def test_apply_rejects_stale_source_versions_before_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, imported = _initialized_workspace(tmp_path, monkeypatch)
    source_ids = (imported["source_id"],)
    initial_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_current_source_version",
        source_ids=source_ids,
        operation=ChangeOperationType.CREATE_PAGE,
        source_versions=_current_source_versions(workspace_path, source_ids),
    )
    applied = runner.invoke(
        app,
        ["apply", initial_changeset, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0, applied.output

    stale_changeset = _stage_routed_page_changeset(
        workspace_path,
        changeset_id="chg_stale_source_version",
        source_ids=source_ids,
        operation=ChangeOperationType.UPDATE_PAGE,
        source_versions=_current_source_versions(workspace_path, source_ids),
    )
    source_path = workspace_path.parent / "cache-policy.md"
    source_path.write_text("Cache policy was updated.\n", encoding="utf-8")
    updated = runner.invoke(
        app,
        ["import", str(source_path), "--workspace", str(workspace_path), "--category", "notes"],
    )
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.stdout)["status"] == "updated"

    stable_page = workspace_path / ROUTED_CANDIDATE_PATH
    stable_content = stable_page.read_text(encoding="utf-8")
    base_commit = _git_head(workspace_path)
    before_source_versions = _applied_source_versions(workspace_path)
    before_page_sources = Workspace.open(workspace_path).page_paths_for_source(
        imported["source_id"]
    )

    failed = runner.invoke(
        app,
        ["apply", stale_changeset, "--approve", "--workspace", str(workspace_path)],
    )

    assert failed.exit_code != 0
    assert "ChangeSet source versions are no longer current" in (failed.stdout + failed.stderr)
    assert _git_head(workspace_path) == base_commit
    assert stable_page.read_text(encoding="utf-8") == stable_content
    assert _applied_source_versions(workspace_path) == before_source_versions
    assert (
        Workspace.open(workspace_path).page_paths_for_source(imported["source_id"])
        == before_page_sources
    )


def test_apply_rejects_duplicate_candidate_source_ownership_before_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path, first = _initialized_workspace(tmp_path, monkeypatch)
    second = _import_extra_source(runner, workspace_path, "storage.md", "Storage is local.\n")
    source_ids = tuple(sorted((first["source_id"], second["source_id"])))
    first_path = "wiki/pages/first.md"
    second_path = "wiki/pages/second.md"
    workspace = Workspace.open(workspace_path)
    changeset = ChangeSet(
        changeset_id="chg_duplicate_page_source",
        base_commit=workspace.current_commit(),
        source_ids=source_ids,
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(type=ChangeOperationType.CREATE_PAGE, path=first_path),
            ChangeOperation(type=ChangeOperationType.CREATE_PAGE, path=second_path),
        ),
    )
    first_page = "---\nsources: " + json.dumps((first["source_id"],)) + "\n---\n"
    second_page = "---\nsources: " + json.dumps(source_ids) + "\n---\n"
    ChangeSetStore(workspace).create(
        changeset,
        {first_path: first_page, second_path: second_page},
    )
    base_commit = _git_head(workspace_path)

    failed = runner.invoke(
        app,
        ["apply", changeset.changeset_id, "--approve", "--workspace", str(workspace_path)],
    )

    assert failed.exit_code != 0
    assert "workspace integrity check failed" in (failed.stdout + failed.stderr)
    assert _git_head(workspace_path) == base_commit
    assert not (workspace_path / first_path).exists()
    assert not (workspace_path / second_path).exists()
    assert _applied_source_versions(workspace_path) == {}
    assert Workspace.open(workspace_path).page_paths_for_source(first["source_id"]) == ()
    assert Workspace.open(workspace_path).page_paths_for_source(second["source_id"]) == ()


def _initialized_workspace(
    tmp_path: Path,
    monkeypatch,
    *,
    source_text: str = SOURCE_TEXT,
) -> tuple[CliRunner, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "cache-policy.md"
    source.write_text(source_text, encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    initialized = runner.invoke(app, ["init", str(workspace)])
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace), "--category", "notes"],
    )

    assert initialized.exit_code == 0
    assert imported.exit_code == 0
    return runner, workspace, json.loads(imported.stdout)


def _stage_changeset(workspace_path: Path, imported: dict[str, Any]) -> str:
    workspace = Workspace.open(workspace_path)
    quote = "Cache entries expire after sixty seconds."
    start = SOURCE_TEXT.index(quote)
    citation = Citation(
        source_id=imported["source_id"],
        content_sha256=imported["content_sha256"],
        snapshot_uri=imported["snapshot_uri"],
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        locator=f"chars:{start}-{start + len(quote)}",
    )
    claim = Claim(
        claim_id="clm_cache_ttl",
        subject="Cache entries",
        predicate="expire after",
        object="sixty seconds",
        status=ClaimStatus.VERIFIED,
        confidence=1.0,
        citations=(citation,),
    )
    changeset = ChangeSet(
        changeset_id="chg_cache_policy",
        base_commit=workspace.current_commit(),
        source_ids=(imported["source_id"],),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path=CANDIDATE_PATH,
            ),
        ),
        claims=(claim,),
    )
    ChangeSetStore(workspace).create(changeset, {CANDIDATE_PATH: CANDIDATE_TEXT})
    return changeset.changeset_id


def _import_extra_source(
    runner: CliRunner,
    workspace_path: Path,
    name: str,
    content: str,
) -> dict[str, Any]:
    source = workspace_path.parent / name
    source.write_text(content, encoding="utf-8")
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace_path), "--category", "notes"],
    )
    assert imported.exit_code == 0, imported.output
    return json.loads(imported.stdout)


def _stage_routed_page_changeset(
    workspace_path: Path,
    *,
    changeset_id: str,
    source_ids: tuple[str, ...],
    operation: ChangeOperationType,
    include_sources: bool = True,
    declared_source_ids: tuple[str, ...] | None = None,
    source_versions: dict[str, int] | None = None,
) -> str:
    workspace = Workspace.open(workspace_path)
    changeset = ChangeSet(
        changeset_id=changeset_id,
        base_commit=workspace.current_commit(),
        source_ids=source_ids,
        source_versions=source_versions or {},
        status=ChangeSetStatus.PROPOSED,
        operations=(ChangeOperation(type=operation, path=ROUTED_CANDIDATE_PATH),),
    )
    page = (
        "---\n"
        'title: "Cache storage"\n'
        "type: concept\n"
        'summary: "Cache storage documentation."\n'
        + (f"sources: {json.dumps(declared_source_ids or source_ids)}\n" if include_sources else "")
        + "---\n\n"
        + "# Cache storage\n"
    )
    ChangeSetStore(workspace).create(changeset, {ROUTED_CANDIDATE_PATH: page})
    return changeset.changeset_id


def _current_source_versions(
    workspace_path: Path,
    source_ids: tuple[str, ...],
) -> dict[str, int]:
    placeholders = ", ".join("?" for _ in source_ids)
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        rows = connection.execute(
            f"""
            SELECT s.source_id, v.id
            FROM sources AS s
            JOIN source_versions AS v ON v.source_id = s.id
            WHERE s.source_id IN ({placeholders}) AND v.is_current = 1
            """,
            source_ids,
        ).fetchall()
    return {str(source_id): int(source_version) for source_id, source_version in rows}


def _applied_source_versions(workspace_path: Path) -> dict[str, int]:
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        rows = connection.execute(
            "SELECT source_id, source_version_id FROM applied_source_versions ORDER BY source_id"
        ).fetchall()
    return {str(source_id): int(source_version) for source_id, source_version in rows}


def _assert_real_citation(
    citation: dict[str, Any],
    imported: dict[str, Any],
    source_text: str,
) -> None:
    assert citation["source_id"] == imported["source_id"]
    assert citation["content_sha256"] == imported["content_sha256"]
    assert citation["snapshot_uri"] == imported["snapshot_uri"]
    start_text, end_text = citation["locator"].removeprefix("chars:").split("-")
    start, end = int(start_text), int(end_text)
    assert source_text[start:end] == citation["quote"]
    assert hashlib.sha256(citation["quote"].encode()).hexdigest() == citation["quote_sha256"]


def _git_head(workspace: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
