from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.compiler import WikiCompiler
from memoryforge.importer import SourceImporter
from memoryforge.lifecycle import ChangeSetLifecycleStore, ChangeSetService
from memoryforge.models import ChangeSetStatus, SourceCategory
from memoryforge.workspace import Workspace


def test_cli_runs_reviewed_changeset_through_apply(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace_path = tmp_path / "wiki"
    source_path = tmp_path / "cache-design.md"
    source_path.write_text(
        "# Cache design\n\nUse namespaced cache keys for tenant isolation.\n",
        encoding="utf-8",
    )

    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    imported = runner.invoke(
        app,
        [
            "import",
            str(source_path),
            "--category",
            "design",
            "--workspace",
            str(workspace_path),
        ],
    )
    source_id = json.loads(imported.stdout)["source"]["source_id"]

    ingested = runner.invoke(app, ["ingest", "--workspace", str(workspace_path)])
    assert ingested.exit_code == 0, ingested.stdout
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert json.loads(ingested.stdout)["status"] == "VALIDATED"
    assert not (workspace_path / "wiki" / "sources" / f"{source_id}.md").exists()

    reviewed = runner.invoke(
        app,
        ["review", changeset_id, "--workspace", str(workspace_path)],
    )
    assert reviewed.exit_code == 0, reviewed.stdout
    assert f"b/wiki/sources/{source_id}.md" in reviewed.stdout
    assert "namespaced cache keys" in reviewed.stdout

    approved = runner.invoke(
        app,
        ["approve", changeset_id, "--workspace", str(workspace_path)],
    )
    assert approved.exit_code == 0, approved.stdout
    assert json.loads(approved.stdout)["status"] == "APPROVED"
    assert not (workspace_path / "wiki" / "sources" / f"{source_id}.md").exists()

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0, applied.stdout
    payload = json.loads(applied.stdout)
    assert payload["status"] == "APPLIED"
    assert len(payload["commit"]) == 40

    page = workspace_path / "wiki" / "sources" / f"{source_id}.md"
    assert f"source_id: {source_id}" in page.read_text(encoding="utf-8")
    assert "L1-L3" in page.read_text(encoding="utf-8")
    assert _git(workspace_path, "log", "-1", "--format=%s") == (
        f"knowledge: apply {changeset_id}"
    )
    assert _git(workspace_path, "status", "--porcelain") == ""


def test_approval_requires_a_recorded_review(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    changeset_id = _compile_one_source(workspace, tmp_path)
    lifecycle = ChangeSetLifecycleStore(workspace)

    try:
        lifecycle.approve(changeset_id)
    except Exception as error:
        assert "Review" in str(error)
    else:
        raise AssertionError("Approval succeeded without review")

    assert lifecycle.get(changeset_id).status is ChangeSetStatus.VALIDATED


def test_apply_refuses_to_overwrite_a_locally_modified_wiki(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    changeset_id = _compile_one_source(workspace, tmp_path)
    service = ChangeSetService(workspace)
    service.review(changeset_id)
    ChangeSetLifecycleStore(workspace).approve(changeset_id)
    (workspace.wiki_dir / "INDEX.md").write_text("# Local edit\n", encoding="utf-8")

    try:
        service.apply(changeset_id)
    except Exception as error:
        assert "uncommitted changes" in str(error)
    else:
        raise AssertionError("Apply overwrote an uncommitted Wiki edit")

    assert (workspace.wiki_dir / "INDEX.md").read_text(encoding="utf-8") == "# Local edit\n"
    assert ChangeSetLifecycleStore(workspace).get(changeset_id).status is (
        ChangeSetStatus.APPROVED
    )


def _compile_one_source(workspace: Workspace, tmp_path: Path) -> str:
    source = tmp_path / "design.md"
    source.write_text("# Design\n\nKeep the source immutable.\n", encoding="utf-8")
    SourceImporter(workspace).import_file(source, category=SourceCategory.DESIGN)
    result = WikiCompiler(workspace).compile_pending()
    changeset_id = result.stored.changeset.changeset_id
    ChangeSetLifecycleStore(workspace).ensure_validated(changeset_id)
    return changeset_id


def _git(workspace_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
