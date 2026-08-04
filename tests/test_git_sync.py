from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.compiler import (
    _load_current_sources,
    _render_deterministic_group_page,
    compile_repository_topics,
)
from memoryforge.importer import SourceValidationError
from memoryforge.linting import lint_workspace
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    Sensitivity,
    TopicGroup,
)
from memoryforge.query import answer_question
from memoryforge.workspace import (
    Workspace,
    WorkspaceError,
    find_applied_page_paths,
    init_workspace,
    list_git_checkouts,
    list_git_code_modules,
    register_git_checkout,
    register_git_code_module,
    search_sources,
    sync_git_checkout,
)


def test_register_and_list_git_checkout_are_idempotent(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nInitial overview")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")

    first = register_git_checkout(workspace, checkout)
    second = register_git_checkout(workspace, checkout)

    assert first == second
    assert list_git_checkouts(workspace) == (first,)
    assert first.checkout_path == str(checkout.resolve())
    assert first.remote_url is None
    assert first.sensitivity == "local_only"


def test_git_add_public_updates_existing_checkout_and_synced_sources(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Public service\n\nPublic documentation only.\n")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")

    registered = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, registered.repository_id)
    assert _current_sensitivity(workspace, first_sync.documents[0].source_id) == "local_only"
    updated = register_git_checkout(
        workspace,
        checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    synced = sync_git_checkout(workspace, updated.repository_id)

    assert registered.sensitivity is Sensitivity.LOCAL_ONLY
    assert updated.sensitivity is Sensitivity.PUBLIC
    assert synced.updated == 1
    assert _current_sensitivity(workspace, synced.documents[0].source_id) == "public"


def test_git_add_public_cli_marks_checkout_public(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Public service\n\nPublic documentation only.\n")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")

    result = CliRunner().invoke(
        app,
        ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["sensitivity"] == "public"


def test_git_sync_uses_remote_identity_and_tracks_source_revisions(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nInitial overview")
    _commit_all(checkout, "Add documentation")
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "https://user:secret@code.example.com/team/service.git?token=ignored",
    )
    workspace = init_workspace(tmp_path / "workspace")

    repository = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, repository.repository_id)
    second_sync = sync_git_checkout(workspace, repository.repository_id)

    identity = "https://code.example.com/team/service.git"
    expected_repository_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    expected_source_id = hashlib.sha256(f"{expected_repository_id}\0README.md".encode()).hexdigest()
    assert repository.repository_id == expected_repository_id
    assert repository.remote_url == identity
    assert "user" not in repository.remote_url
    assert "secret" not in repository.remote_url
    assert first_sync.created == 1
    assert first_sync.updated == 0
    assert first_sync.unchanged == 0
    assert first_sync.documents[0].source_id == expected_source_id
    assert second_sync.created == 0
    assert second_sync.updated == 0
    assert second_sync.unchanged == 1
    assert _current_version_count(workspace, expected_source_id) == 1
    assert _current_sensitivity(workspace, expected_source_id) == "local_only"

    _write(checkout / "README.md", "# Service\n\nUpdated overview")
    _commit_all(checkout, "Update documentation")
    updated_sync = sync_git_checkout(workspace, repository.repository_id)

    assert updated_sync.created == 0
    assert updated_sync.updated == 1
    assert updated_sync.unchanged == 0
    assert updated_sync.documents[0].source_id == expected_source_id
    assert _current_version_count(workspace, expected_source_id) == 1
    assert _source_version_count(workspace, expected_source_id) == 2
    assert _current_git_revision(workspace, expected_source_id) == updated_sync.head_commit


def test_git_documents_get_a_repository_overview_that_rebuilds_on_update(
    tmp_path: Path,
) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nThe service accepts orders.\n")
    _write(
        checkout / "docs" / "retry.md",
        "# Retry policy\n\nRetries stop after three attempts.\n",
    )
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)

    runner = CliRunner()
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    stored = ChangeSetStore(Workspace.open(workspace)).get(changeset_id)
    overview_path = f"wiki/pages/repository-{repository.repository_id[:12]}.md"
    overview = stored.candidate_files[overview_path]
    assert "generated: repository_overview" in overview
    assert "[README](" in overview
    assert "[retry](" in overview

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    assert lint_workspace(workspace) == {
        "status": "clean",
        "checked_pages": 3,
        "issues": [],
    }

    _write(
        checkout / "docs" / "timeout.md",
        "# Timeout policy\n\nRequests stop after thirty seconds.\n",
    )
    _commit_all(checkout, "Add timeout policy")
    sync_git_checkout(workspace, repository.repository_id)
    updated = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert updated.exit_code == 0, updated.output
    next_stored = ChangeSetStore(Workspace.open(workspace)).get(
        json.loads(updated.stdout)["changeset_id"]
    )
    assert overview_path in next_stored.candidate_files
    assert "[timeout](" in next_stored.candidate_files[overview_path]


def test_public_repository_topics_only_change_the_navigation_page(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nThe service accepts orders.\n")
    _write(
        checkout / "docs" / "retry.md",
        "# Retry policy\n\nRetries stop after three attempts.\n",
    )
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(
        workspace,
        checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    synced = sync_git_checkout(workspace, repository.repository_id)
    source_ids = {document.relative_path: document.source_id for document in synced.documents}
    runner = CliRunner()
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    initial_id = json.loads(staged.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", initial_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output

    class FakeTopicProvider:
        def __init__(self) -> None:
            self.messages: tuple[dict[str, str], ...] | None = None

        def organize_topics(
            self,
            messages: tuple[dict[str, str], ...],
        ) -> tuple[TopicGroup, ...]:
            self.messages = messages
            return (
                TopicGroup(
                    title="Service overview",
                    summary="What the service does.",
                    source_ids=(source_ids["README.md"],),
                ),
                TopicGroup(
                    title="Reliability",
                    summary="How retries are controlled.",
                    source_ids=(source_ids["docs/retry.md"],),
                ),
            )

    provider = FakeTopicProvider()
    compilation = compile_repository_topics(
        Workspace.open(workspace),
        repository.repository_id,
        provider,  # type: ignore[arg-type]
    )
    overview_path = f"wiki/pages/repository-{repository.repository_id[:12]}.md"
    assert compilation.changeset.source_ids == ()
    assert set(compilation.candidate_files) == {overview_path, "wiki/INDEX.md"}
    assert "## 主题导航" in compilation.candidate_files[overview_path]
    assert "### Reliability" in compilation.candidate_files[overview_path]
    assert provider.messages is not None
    assert "The service accepts orders." in provider.messages[1]["content"]
    assert "Retries stop after three attempts." in provider.messages[1]["content"]

    stored = ChangeSetStore(Workspace.open(workspace)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    applied_topics = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied_topics.exit_code == 0, applied_topics.output
    assert lint_workspace(workspace)["status"] == "clean"

    _write(
        checkout / "docs" / "retry.md",
        "# Retry policy\n\nRetries stop after five attempts.\n",
    )
    _commit_all(checkout, "Update retry policy")
    sync_git_checkout(workspace, repository.repository_id)
    refreshed = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert refreshed.exit_code == 0, refreshed.output
    refreshed_stored = ChangeSetStore(Workspace.open(workspace)).get(
        json.loads(refreshed.stdout)["changeset_id"]
    )
    refreshed_overview = refreshed_stored.candidate_files[overview_path]
    assert "topic_groups:" in refreshed_overview
    assert "### Reliability" in refreshed_overview


def test_selected_code_module_builds_a_cited_page_and_tracks_updates(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Meter service\n\nRecords service usage.\n")
    _write(
        checkout / "internal" / "meter" / "meter.go",
        """package meter

type Meter struct {}

func NewMeter() *Meter {
	return &Meter{}
}

func (m *Meter) RecordUsage() {}
""",
    )
    _write(checkout / "internal" / "meter" / "__init__.py", "")
    _write(checkout / "cmd" / "main.py", "def main():\n    pass\n")
    _commit_all(checkout, "Add meter module")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)

    runner = CliRunner()
    selected = runner.invoke(
        app,
        [
            "code-add",
            repository.repository_id,
            "internal/meter/",
            "--workspace",
            str(workspace),
        ],
    )
    assert selected.exit_code == 0, selected.output
    assert json.loads(selected.stdout)["path"] == "internal/meter"
    assert list_git_code_modules(Workspace.open(workspace), repository.repository_id) == (
        "internal/meter",
    )

    synced = sync_git_checkout(workspace, repository.repository_id)
    code_document = next(
        document for document in synced.documents if document.relative_path.endswith("meter.go")
    )
    assert {document.relative_path for document in synced.documents} == {
        "README.md",
        "internal/meter/meter.go",
    }
    assert code_document.status == "created"

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    code_page = (
        ChangeSetStore(Workspace.open(workspace))
        .get(changeset_id)
        .candidate_files[f"wiki/pages/{code_document.source_id}.md"]
    )
    assert "# Code: internal/meter/meter.go" in code_page
    assert "### internal/meter/meter.go" in code_page
    assert "- package meter [^source-1]" in code_page
    assert "- type Meter [^source-2]" in code_page
    assert "- func NewMeter [^source-3]" in code_page
    assert "- func (m *Meter) RecordUsage [^source-4]" in code_page

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    assert lint_workspace(workspace)["status"] == "clean"
    answer = runner.invoke(app, ["ask", "NewMeter", "--workspace", str(workspace)])
    assert answer.exit_code == 0, answer.output
    assert json.loads(answer.stdout)["quote"] == "func NewMeter"

    _write(
        checkout / "internal" / "meter" / "meter.go",
        """package meter

type Meter struct {}

func NewMeter() *Meter {
	return &Meter{}
}

func (m *Meter) RecordUsage() {}

func Reset() {}
""",
    )
    _commit_all(checkout, "Add meter reset")
    refreshed = sync_git_checkout(workspace, repository.repository_id)
    assert refreshed.created == 0
    assert refreshed.updated == 1
    assert refreshed.unchanged == 1
    assert _current_git_revision(workspace, code_document.source_id) == refreshed.head_commit


def test_whole_repository_code_selection_skips_secrets_and_adds_module_sources(
    tmp_path: Path,
) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nRepository overview.\n")
    _write(checkout / "cmd" / "main.go", "package main\n\nfunc main() {}\n")
    _write(checkout / "internal" / "meter.go", "package internal\n\ntype Meter struct{}\n")
    _write(
        checkout / "internal" / "private.go",
        'package internal\n\nconst API_KEY = "' + "sk-" + "a" * 26 + '"\n',
    )
    _commit_all(checkout, "Add repository code")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)

    selected = CliRunner().invoke(
        app,
        ["code-add", repository.repository_id, ".", "--workspace", str(workspace)],
    )
    assert selected.exit_code == 0, selected.output
    assert json.loads(selected.stdout)["path"] == "."

    synced = sync_git_checkout(workspace, repository.repository_id)
    paths = {document.relative_path for document in synced.documents}
    assert synced.skipped == ("internal/private.go",)
    assert "cmd/main.go" in paths
    assert "internal/meter.go" in paths
    assert "internal/private.go" not in paths
    assert ".memoryforge/code-modules/cmd.md" in paths
    assert ".memoryforge/code-modules/internal.md" in paths
    module = search_sources(workspace, "Code module internal")[0]
    module_content = (workspace / module.snapshot_path).read_text(encoding="utf-8")
    assert "Canonical module path: `internal`" in module_content
    assert "Search aliases: `internal`" in module_content
    assert "Contains 2 tracked Go/Python files" in module_content
    assert "Main exported operations in `internal`: `Meter`" in module_content


def test_whole_repository_adds_a_card_for_each_nested_code_module(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(
        checkout / "services" / "accounts" / "service.go",
        'package accounts\n\nimport "example/services/jobs"\n\n'
        "func CreateAccount() {}\n"
        "func DescribeAccount() {}\n"
        "func RegisterRoutes() {}\n"
        "func HandleCreate() { jobs.StartTask() }\n",
    )
    _write(
        checkout / "services" / "accounts" / "service_test.go",
        "package accounts\n\nfunc TestCreateAccount() {}\n",
    )
    _write(checkout / "services" / "jobs" / "task.go", "package jobs\n\nfunc StartTask() {}\n")
    _commit_all(checkout, "Add nested modules")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    register_git_code_module(workspace, repository.repository_id, ".")

    synced = sync_git_checkout(workspace, repository.repository_id)
    paths = {document.relative_path for document in synced.documents}

    assert ".memoryforge/code-modules/services.md" in paths
    assert ".memoryforge/code-modules/services/accounts.md" in paths
    accounts = search_sources(workspace, '"Code module" "services/accounts"')[0]
    content = (workspace / accounts.snapshot_path).read_text(encoding="utf-8")
    assert "# Code module: services/accounts" in content
    assert "Search aliases: `accounts`, `services/accounts`" in content
    assert "`CreateAccount`" in content
    assert "`services/accounts/service.go`" in content
    assert "## Entry points and handlers" in content
    assert "`RegisterRoutes`" in content
    assert "`HandleCreate`" in content
    assert "Imports module `services/jobs`" in content
    assert "`services/accounts/service_test.go`" in content


def test_git_sync_rejects_checkout_with_changed_repository_identity(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nOriginal repository evidence")
    _commit_all(checkout, "Add documentation")
    _git(checkout, "remote", "add", "origin", "https://code.example.com/team/service.git")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, repository.repository_id)
    source_id = first_sync.documents[0].source_id

    _git(checkout, "remote", "set-url", "origin", "https://code.example.com/team/other.git")

    with pytest.raises(WorkspaceError, match="identity changed"):
        sync_git_checkout(workspace, repository.repository_id)

    assert _current_version_count(workspace, source_id) == 1
    assert _last_synced_commit(workspace, repository.repository_id) == first_sync.head_commit
    assert [result.source_id for result in search_sources(workspace, "Original repository")] == [
        source_id
    ]


def test_git_sync_deactivates_deleted_document_source(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nRetired documentation evidence")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, repository.repository_id)
    source_id = first_sync.documents[0].source_id

    (checkout / "README.md").unlink()
    _commit_all(checkout, "Remove documentation")
    deleted_sync = sync_git_checkout(workspace, repository.repository_id)

    assert deleted_sync.documents == ()
    assert _current_version_count(workspace, source_id) == 0
    assert search_sources(workspace, "Retired documentation") == []


def test_deleted_git_document_generates_reviewable_archive_and_apply_removes_page(
    tmp_path: Path,
) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nRetired documentation evidence")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, repository.repository_id)
    source_id = first_sync.documents[0].source_id
    runner = CliRunner()

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    initial_id = json.loads(staged.stdout)["changeset_id"]
    assert (
        runner.invoke(
            app,
            ["apply", initial_id, "--approve", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    page_path = workspace / "wiki/pages" / f"{source_id}.md"
    assert page_path.is_file()

    (checkout / "README.md").unlink()
    _commit_all(checkout, "Remove documentation")
    sync_git_checkout(workspace, repository.repository_id)

    linted = lint_workspace(workspace)
    assert [issue["code"] for issue in linted["issues"]] == ["cleanup_required"]
    cleanup = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert cleanup.exit_code == 0, cleanup.output
    cleanup_id = json.loads(cleanup.stdout)["changeset_id"]
    stored = ChangeSetStore(Workspace.open(workspace)).get(cleanup_id)
    assert any(
        operation.type is ChangeOperationType.ARCHIVE_PAGE
        and operation.path == f"wiki/pages/{source_id}.md"
        for operation in stored.changeset.operations
    )
    assert f"wiki/pages/{source_id}.md" not in stored.candidate_files
    assert f"pages/{source_id}.md" not in stored.candidate_files["wiki/INDEX.md"]

    applied = runner.invoke(
        app,
        ["apply", cleanup_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    assert not page_path.exists()
    assert _source_version_count(workspace, source_id) == 1
    assert _current_version_count(workspace, source_id) == 0
    assert lint_workspace(workspace)["status"] == "clean"


def test_deleted_source_rebuilds_shared_page_from_remaining_source(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nThe service accepts orders.\n")
    _write(checkout / "docs" / "retry.md", "# Retry policy\n\nRetries stop after three attempts.\n")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)
    sources = _load_current_sources(Workspace.open(workspace), set())
    source_by_path = {source.relative_path: source for source in sources}
    shared_path = "wiki/pages/shared.md"
    shared_content = _render_deterministic_group_page(Workspace.open(workspace), list(sources))
    index_content = (
        "# Knowledge Index\n\nUse this page to find the relevant Wiki page before reading it.\n\n"
        "## Concepts\n\n- [README / retry policy](pages/shared.md) — shared documentation\n"
    )
    changeset = ChangeSetStore(Workspace.open(workspace)).create(
        ChangeSet(
            changeset_id="chg_shared_page",
            base_commit=Workspace.open(workspace).current_commit(),
            source_ids=tuple(source.source_id for source in sources),
            source_versions={source.source_id: source.source_version for source in sources},
            status=ChangeSetStatus.PROPOSED,
            operations=(
                ChangeOperation(
                    type=ChangeOperationType.CREATE_PAGE,
                    path=shared_path,
                ),
                ChangeOperation(
                    type=ChangeOperationType.CREATE_PAGE,
                    path="wiki/INDEX.md",
                ),
            ),
        ),
        {shared_path: shared_content, "wiki/INDEX.md": index_content},
    )
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            ["apply", changeset.changeset.changeset_id, "--approve", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )

    (checkout / "README.md").unlink()
    _commit_all(checkout, "Remove service overview")
    sync_git_checkout(workspace, repository.repository_id)

    cleanup = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert cleanup.exit_code == 0, cleanup.output
    cleanup_id = json.loads(cleanup.stdout)["changeset_id"]
    stored = ChangeSetStore(Workspace.open(workspace)).get(cleanup_id)
    assert any(
        operation.type is ChangeOperationType.UPDATE_PAGE and operation.path == shared_path
        for operation in stored.changeset.operations
    )
    assert "Service" not in stored.candidate_files[shared_path]
    assert "Retries stop after three attempts." in stored.candidate_files[shared_path]

    applied = runner.invoke(
        app,
        ["apply", cleanup_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    assert (workspace / shared_path).is_file()
    retry_source_id = source_by_path["docs/retry.md"].source_id
    assert Workspace.open(workspace).page_paths_for_source(retry_source_id) == (shared_path,)
    assert lint_workspace(workspace)["status"] == "clean"


def test_repository_scope_keeps_same_keyword_queries_in_one_git_checkout(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    first_root.mkdir()
    first_checkout = _create_repository(first_root)
    _write(
        first_checkout / "README.md",
        "# Shared module\n\nRepository one owns the blue scheduler.\n",
    )
    _commit_all(first_checkout, "Add first scheduler")
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_checkout = _create_repository(second_root)
    _write(
        second_checkout / "README.md",
        "# Shared module\n\nRepository two owns the red scheduler.\n",
    )
    _commit_all(second_checkout, "Add second scheduler")
    workspace = init_workspace(tmp_path / "workspace")
    first_repository = register_git_checkout(workspace, first_checkout)
    second_repository = register_git_checkout(workspace, second_checkout)
    sync_git_checkout(workspace, first_repository.repository_id)
    sync_git_checkout(workspace, second_repository.repository_id)
    runner = CliRunner()
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    assert (
        runner.invoke(
            app,
            [
                "apply",
                json.loads(staged.stdout)["changeset_id"],
                "--approve",
                "--workspace",
                str(workspace),
            ],
        ).exit_code
        == 0
    )

    scoped_sources = search_sources(
        workspace,
        "scheduler",
        repository_id=first_repository.repository_id,
    )
    scoped_pages = find_applied_page_paths(
        workspace,
        "scheduler",
        repository_id=first_repository.repository_id,
    )
    scoped_answer = answer_question(
        workspace,
        "Which repository owns the scheduler?",
        repository_id=first_repository.repository_id,
    )

    assert len(scoped_sources) == 1
    assert scoped_sources[0].source_path == "README.md"
    assert len(scoped_pages) == 1
    assert scoped_answer["status"] == "answered"
    assert "blue scheduler" in scoped_answer["answer"]
    assert "red scheduler" not in scoped_answer["answer"]


def test_git_sync_preflights_all_documents_before_importing_any(tmp_path: Path) -> None:
    checkout = _create_repository(tmp_path)
    _write(checkout / "README.md", "# Service\n\nInitial clean documentation")
    _write(checkout / "docs" / "token.md", "# Token\n\nInitial safe documentation")
    _commit_all(checkout, "Add documentation")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    first_sync = sync_git_checkout(workspace, repository.repository_id)
    readme_source_id = next(
        document.source_id
        for document in first_sync.documents
        if document.relative_path == "README.md"
    )

    _write(checkout / "README.md", "# Service\n\nUpdated clean documentation")
    _write(
        checkout / "docs" / "token.md",
        "# Token\n\nAPI_TOKEN=supersecrettokenvalue",
    )
    _commit_all(checkout, "Update documentation")

    with pytest.raises(SourceValidationError, match="high-confidence secret"):
        sync_git_checkout(workspace, repository.repository_id)

    assert _source_version_count(workspace, readme_source_id) == 1
    assert [result.source_id for result in search_sources(workspace, "Initial clean")] == [
        readme_source_id
    ]
    assert search_sources(workspace, "Updated clean") == []
    assert _last_synced_commit(workspace, repository.repository_id) == first_sync.head_commit


def _current_version_count(workspace: Path, source_id: str) -> int:
    with _connection(workspace) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM source_versions AS versions
            JOIN sources AS sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()
    return int(row["count"])


def _source_version_count(workspace: Path, source_id: str) -> int:
    with _connection(workspace) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM source_versions AS versions
            JOIN sources AS sources ON sources.id = versions.source_id
            WHERE sources.source_id = ?
            """,
            (source_id,),
        ).fetchone()
    return int(row["count"])


def _current_sensitivity(workspace: Path, source_id: str) -> str:
    with _connection(workspace) as connection:
        row = connection.execute(
            """
            SELECT versions.sensitivity
            FROM source_versions AS versions
            JOIN sources AS sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()
    assert row is not None
    return str(row["sensitivity"])


def _current_git_revision(workspace: Path, source_id: str) -> str:
    with _connection(workspace) as connection:
        row = connection.execute(
            """
            SELECT revisions.commit_sha
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions ON versions.id = revisions.source_version_id
            JOIN sources AS sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()
    assert row is not None
    return str(row["commit_sha"])


def _last_synced_commit(workspace: Path, repository_id: str) -> str | None:
    with _connection(workspace) as connection:
        row = connection.execute(
            "SELECT last_synced_commit FROM git_repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
    assert row is not None
    return str(row["last_synced_commit"]) if row["last_synced_commit"] is not None else None


def _connection(workspace: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(workspace / ".memoryforge" / "index.sqlite")
    connection.row_factory = sqlite3.Row
    return connection


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


def _commit_all(checkout: Path, message: str) -> None:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", message)


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=checkout, check=True, capture_output=True, text=True)
