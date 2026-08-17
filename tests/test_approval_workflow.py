from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.core.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
)
from memoryforge.interface.cli import app
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace
from tests.cli_helpers import review_approve_apply


def test_review_approve_apply_records_separate_lifecycle_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)

    reviewed = runner.invoke(
        app,
        ["review", changeset_id, "--workspace", str(workspace)],
    )
    assert reviewed.exit_code == 0, reviewed.output
    assert json.loads(reviewed.stdout)["reviewed_at"]

    approved = runner.invoke(
        app,
        ["approve", changeset_id, "--workspace", str(workspace)],
    )
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.stdout)["status"] == "APPROVED"
    assert not any((workspace / "wiki/pages").glob("*.md"))

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["status"] == "APPLIED"
    archived = workspace / ".memoryforge/staging/applied" / changeset_id
    review_receipt = json.loads((archived / "review.json").read_text(encoding="utf-8"))
    assert review_receipt["review_mode"] == "displayed"
    assert (archived / "approval.json").is_file()


def test_approve_requires_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["approve", changeset_id, "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "Review the ChangeSet before approval" in result.output


def test_apply_rejects_tampered_approval_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    assert (
        runner.invoke(
            app,
            ["review", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            ["approve", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    approval_path = workspace / ".memoryforge/staging" / changeset_id / "approval.json"
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    payload["proposal_sha256"] = "0" * 64
    approval_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply", changeset_id, "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "integrity check failed" in result.output
    assert not any((workspace / "wiki/pages").glob("*.md"))


def test_apply_rejects_candidate_quote_not_grounded_by_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, _ = _staged_changeset(tmp_path, monkeypatch)
    source = json.loads(runner.invoke(app, ["source-list", "--workspace", str(workspace)]).stdout)[
        0
    ]
    opened = Workspace.open(workspace)
    changeset_id = "chg_invalid_quote"
    page_path = f"wiki/pages/{source['source_id']}.md"
    candidate = (
        "---\n"
        'title: "Invalid"\n'
        "type: concept\n"
        'summary: "Invalid"\n'
        f'sources: ["{source["source_id"]}"]\n'
        "---\n\n"
        "# Invalid\n\n"
        "## Verified facts\n\n"
        "- Fabricated fact. [^source-1]\n\n"
        f"[^source-1]: source `{source['source_id']}` · revision "
        f"`{source['version_id']}` · `chars:16-57`\n"
    )
    ChangeSetStore(opened).create(
        ChangeSet(
            changeset_id=changeset_id,
            base_commit=opened.current_commit(),
            source_ids=(source["source_id"],),
            source_versions={source["source_id"]: source["version_id"]},
            status=ChangeSetStatus.PROPOSED,
            operations=(ChangeOperation(type=ChangeOperationType.CREATE_PAGE, path=page_path),),
        ),
        {page_path: candidate},
    )

    result = review_approve_apply(runner, changeset_id, workspace)

    assert result.exit_code != 0
    assert "workspace integrity check failed" in result.output
    assert not (workspace / page_path).exists()


def test_apply_rejects_removed_inline_approval_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "No such option" in result.output
    assert not any((workspace / "wiki/pages").glob("*.md"))


def test_apply_warns_without_rollback_when_obsidian_rebuild_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)

    def fail_obsidian(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Obsidian rebuild failed")

    monkeypatch.setattr("memoryforge.cli.build_obsidian", fail_obsidian)

    result = review_approve_apply(runner, changeset_id, workspace)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "APPLIED"
    assert payload["obsidian"]["status"] == "failed"
    assert "Wiki applied successfully" in payload["obsidian"]["warning"]
    assert "Obsidian rebuild failed" in payload["obsidian"]["warning"]
    assert list((workspace / "wiki/pages").glob("*.md"))


def _staged_changeset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CliRunner, Path, str]:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    initialized = runner.invoke(app, ["init", str(workspace)])
    assert initialized.exit_code == 0, initialized.output
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )
    assert ingested.exit_code == 0, ingested.output
    return runner, workspace, json.loads(ingested.stdout)["changeset_id"]
