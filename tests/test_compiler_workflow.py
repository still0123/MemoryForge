from __future__ import annotations

import hashlib
import json
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


def test_ingest_pending_compiles_imported_source_to_proposed_changeset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _initialized_workspace(tmp_path, monkeypatch)

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
    assert payload["claims"]
    verified = [claim for claim in payload["claims"] if claim["status"] == "VERIFIED"]
    assert verified
    for claim in verified:
        assert claim["citations"]
        for citation in claim["citations"]:
            _assert_real_citation(citation, imported, SOURCE_TEXT)


def test_review_is_read_only_and_returns_candidates_diff_and_claims(
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
    assert payload["claims"]
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
    runner, workspace, imported = _initialized_workspace(
        tmp_path,
        monkeypatch,
        source_text=MULTILINE_SOURCE_TEXT,
    )

    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])

    assert ingested.exit_code == 0
    proposal = json.loads(ingested.stdout)
    citation = proposal["claims"][0]["citations"][0]
    _assert_real_citation(citation, imported, MULTILINE_SOURCE_TEXT)
    assert citation["quote"] == (
        "The cache uses namespaced keys\nand expires entries after sixty seconds."
    )

    reviewed = runner.invoke(
        app,
        ["review", proposal["changeset_id"], "--workspace", str(workspace)],
    )
    assert reviewed.exit_code == 0
    review = json.loads(reviewed.stdout)
    candidate_path = proposal["files"][0]
    candidate = review["candidate_files"][candidate_path]
    assert f"`{citation['snapshot_uri']}`" in candidate
    assert f"`{citation['locator']}`" in candidate
    assert f"source `{citation['source_id']}`" in candidate


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
    target_path = f"wiki/sources/{imported['source_id']}.md"
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
