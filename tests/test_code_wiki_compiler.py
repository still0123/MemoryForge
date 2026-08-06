from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.code_index import build_code_index
from memoryforge.code_models import make_code_wiki_path
from memoryforge.code_wiki_compiler import (
    CodeWikiCompilationError,
    compile_code_wiki,
)
from memoryforge.linting import lint_workspace
from memoryforge.module_planner import build_architecture_graph, build_module_plan
from memoryforge.workspace import (
    Workspace,
    init_workspace,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

SERVICE_SOURCE = """def helper(name: str) -> str:
    return f"Hello {name}"


class Service:
    def greet(self, name: str) -> str:
        return helper(name)
"""


def test_code_wiki_compiles_reviews_applies_and_lints_nested_pages(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    graph = build_architecture_graph(snapshot, plan)

    compilation = compile_code_wiki(workspace, snapshot, plan, graph)

    assert compilation is not None
    assert compilation.changeset.status.value == "PROPOSED"
    src_page = make_code_wiki_path(repository_id, "src")
    service_page = make_code_wiki_path(repository_id, "src/service")
    assert set(compilation.candidate_files) == {
        "wiki/INDEX.md",
        src_page,
        service_page,
    }
    parent = compilation.candidate_files[src_page]
    leaf = compilation.candidate_files[service_page]
    assert "generated: code_module_overview" in parent
    assert "sources:" not in parent
    assert "[Service](src/service.md)" in parent
    assert "generated: code_wiki" in leaf
    assert "## Verified symbols" in leaf
    assert "src.service.Service.greet" in leaf
    assert "revision `" in leaf
    assert compilation.changeset.validation is not None
    assert compilation.changeset.validation.citation_coverage == 1.0
    assert all(
        operation.details.get("origin") == "code_wiki"
        for operation in compilation.changeset.operations
    )

    runner = CliRunner()
    ingested = runner.invoke(
        app,
        [
            "ingest",
            "--code-wiki",
            repository_id,
            "--workspace",
            str(workspace),
        ],
    )
    assert ingested.exit_code == 0, ingested.output
    stored = ChangeSetStore(Workspace.open(workspace)).get(
        json.loads(ingested.stdout)["changeset_id"]
    )
    assert stored.changeset == compilation.changeset
    assert not (workspace / service_page).exists()
    reviewed = runner.invoke(
        app,
        ["review", stored.changeset.changeset_id, "--workspace", str(workspace)],
    )
    approved = runner.invoke(
        app,
        ["approve", stored.changeset.changeset_id, "--workspace", str(workspace)],
    )
    applied = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--workspace", str(workspace)],
    )

    assert reviewed.exit_code == 0, reviewed.output
    assert approved.exit_code == 0, approved.output
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["status"] == "APPLIED"
    assert lint_workspace(workspace) == {
        "status": "clean",
        "checked_pages": 2,
        "issues": [],
    }
    source_id = next(iter(snapshot.source_versions))
    assert Workspace.open(workspace).page_paths_for_source(source_id) == (service_page,)
    assert compile_code_wiki(workspace, snapshot, plan, graph) is None


def test_code_wiki_rejects_a_forged_symbol_body_hash(tmp_path: Path) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    forged_symbol = snapshot.symbols[0].model_copy(update={"body_sha256": "0" * 64})
    forged = snapshot.model_copy(update={"symbols": (forged_symbol, *snapshot.symbols[1:])})
    plan = build_module_plan(forged)

    with pytest.raises(CodeWikiCompilationError, match="evidence hash"):
        compile_code_wiki(workspace, forged, plan)


def test_code_wiki_archives_modules_removed_from_the_current_snapshot(
    tmp_path: Path,
) -> None:
    checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/service.py": SERVICE_SOURCE,
            "src/legacy.py": "def legacy() -> str:\n    return 'old'\n",
        },
    )
    _compile_and_apply(workspace, repository_id)
    legacy_page = workspace / make_code_wiki_path(repository_id, "src/legacy")
    assert legacy_page.is_file()

    (checkout / "src/legacy.py").unlink()
    _commit_all(checkout, "Remove legacy module")
    sync_git_checkout(workspace, repository_id)
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    compilation = compile_code_wiki(workspace, snapshot, plan)

    assert compilation is not None
    archived = {
        operation.path
        for operation in compilation.changeset.operations
        if operation.type.value == "ARCHIVE_PAGE"
    }
    assert archived == {make_code_wiki_path(repository_id, "src/legacy")}
    stored = ChangeSetStore(Workspace.open(workspace)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    result = CliRunner().invoke(
        app,
        [
            "apply",
            stored.changeset.changeset_id,
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not legacy_page.exists()
    assert lint_workspace(workspace)["status"] == "clean"


def _compile_and_apply(workspace: Path, repository_id: str) -> None:
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    compilation = compile_code_wiki(workspace, snapshot, plan)
    assert compilation is not None
    stored = ChangeSetStore(Workspace.open(workspace)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    result = CliRunner().invoke(
        app,
        [
            "apply",
            stored.changeset.changeset_id,
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output


def _synced_repository(
    tmp_path: Path,
    files: dict[str, str],
) -> tuple[Path, Path, str]:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n", encoding="utf-8")
    for relative_path, content in files.items():
        target = checkout / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _commit_all(checkout, "Add service")

    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)
    register_git_code_module(workspace, repository.repository_id, "src")
    sync_git_checkout(workspace, repository.repository_id)
    return checkout, workspace, repository.repository_id


def _commit_all(checkout: Path, message: str) -> None:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", message)


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
