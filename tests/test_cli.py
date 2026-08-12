from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

import memoryforge.cli as cli_module
from memoryforge.cli import app
from memoryforge.sessions import SessionStore
from tests.cli_helpers import review_approve_apply


def test_cli_version_exits_without_a_subcommand() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "0.4.0\n"


def test_cli_init_import_and_search_local_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "public-note.md"
    source.write_text("# Public note\n\nA searchable local knowledge fixture.", encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init", str(workspace)])
    import_result = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace), "--category", "notes"],
    )
    search_result = runner.invoke(
        app,
        ["search", "searchable", "--workspace", str(workspace)],
    )

    assert init_result.exit_code == 0
    assert import_result.exit_code == 0
    imported = json.loads(import_result.stdout)
    assert imported["status"] == "created"
    assert imported["source_uri"].startswith("mf://source/")
    assert imported["snapshot_uri"].startswith("mf://blob/")
    assert str(tmp_path) not in import_result.stdout
    assert search_result.exit_code == 0
    searched = json.loads(search_result.stdout)[0]
    assert searched["title"] == "Public note"
    assert searched["source_id"] == imported["source_id"]
    assert searched["source_uri"] == imported["source_uri"]
    assert searched["source_path"] == "public-note.md"
    assert searched["snapshot_uri"] == imported["snapshot_uri"]
    assert str(tmp_path) not in search_result.stdout
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        sensitivity = connection.execute(
            """
            SELECT versions.sensitivity
            FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (imported["source_id"],),
        ).fetchone()[0]
    assert sensitivity == "local_only"


def test_cli_import_requires_explicit_public_opt_in(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "public.md"
    source.write_text("# Public\n\nPublic fixture.\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    imported = runner.invoke(
        app,
        ["import", str(source), "--public", "--workspace", str(workspace)],
    )

    assert imported.exit_code == 0, imported.output
    source_id = json.loads(imported.stdout)["source_id"]
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        sensitivity = connection.execute(
            """
            SELECT versions.sensitivity
            FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()[0]
    assert sensitivity == "public"


def test_cli_reports_integrity_failure_without_traceback_or_private_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "public-note.md"
    source.write_text("# Public note\n\nsearchable evidence", encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace)],
    )
    snapshot_path = workspace / json.loads(imported.stdout)["snapshot_path"]
    snapshot_path.write_text("tampered", encoding="utf-8")

    result = runner.invoke(app, ["search", "searchable", "--workspace", str(workspace)])
    output = result.stdout + result.stderr

    assert result.exit_code == 1
    assert "integrity" in output.lower()
    assert "Traceback" not in output
    assert str(tmp_path) not in output


def test_cli_help_describes_source_version_category() -> None:
    result = CliRunner().invoke(app, ["import", "--help"])

    assert result.exit_code == 0
    assert "SourceVersion category" in result.stdout


def test_query_commands_expose_repository_scope() -> None:
    commands = get_command(app).commands

    for name in ("search", "ask", "agent"):
        assert any(
            "--repository" in getattr(parameter, "opts", ())
            and not getattr(parameter, "hidden", False)
            for parameter in commands[name].params
        )


def test_cli_agent_eval_exposes_bounds_and_local_authorization() -> None:
    command = get_command(app).commands["agent-eval"]

    assert any(
        "--max-steps" in getattr(parameter, "opts", ()) and not getattr(parameter, "hidden", False)
        for parameter in command.params
    )
    assert any(
        "--max-pages" in getattr(parameter, "opts", ()) and not getattr(parameter, "hidden", False)
        for parameter in command.params
    )
    assert any(
        "--allow-local-llm" in getattr(parameter, "opts", ())
        and not getattr(parameter, "hidden", False)
        for parameter in command.params
    )


def test_cli_agent_eval_forwards_local_authorization(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    suite = tmp_path / "suite.json"
    suite.write_text("{}", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    captured: dict[str, bool] = {}

    monkeypatch.setenv("MEMORYFORGE_API_BASE", "https://example.invalid")
    monkeypatch.setenv("MEMORYFORGE_API_KEY", "test")
    monkeypatch.setenv("MEMORYFORGE_MODEL", "test")

    def fake_evaluation(
        _workspace: Path,
        _suite: Path,
        _provider: object,
        **kwargs: object,
    ) -> dict[str, object]:
        captured["allow_local"] = bool(kwargs["allow_local"])
        return {"case_count": 0}

    monkeypatch.setattr(cli_module, "run_agent_evaluation", fake_evaluation)

    result = runner.invoke(
        app,
        [
            "agent-eval",
            str(suite),
            "--allow-local-llm",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"allow_local": True}


def test_agent_clear_removes_one_local_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    SessionStore(workspace, "chat-clear").append(
        "old question",
        "old answer",
        [],
        model_safe=True,
    )

    result = runner.invoke(
        app,
        ["agent-clear", "chat-clear", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "session_id": "chat-clear",
        "status": "CLEARED",
    }
    assert SessionStore(workspace, "chat-clear").load(allow_local=True) == []


def test_codex_setup_installs_one_idempotent_recall_block(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text("# Existing instructions\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    command = ["codex-setup", str(project), "--workspace", str(workspace)]
    first = runner.invoke(app, command)
    second = runner.invoke(app, command)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    content = agents.read_text(encoding="utf-8")
    assert content.startswith("# Existing instructions\n")
    assert content.count("BEGIN MEMORYFORGE RECALL") == 1
    assert "memoryforge recall --workspace" in content


def test_cli_registers_lists_and_syncs_existing_git_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n\nCLI fixture", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    add_result = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    repository = json.loads(add_result.stdout)
    list_result = runner.invoke(app, ["git-list", "--workspace", str(workspace)])
    sync_result = runner.invoke(
        app,
        ["git-sync", repository["repository_id"], "--workspace", str(workspace)],
    )
    unknown_result = runner.invoke(
        app,
        ["git-sync", "0" * 64, "--workspace", str(workspace)],
    )

    assert add_result.exit_code == 0
    assert repository["sensitivity"] == "local_only"
    assert list_result.exit_code == 0
    assert [item["repository_id"] for item in json.loads(list_result.stdout)] == [
        repository["repository_id"]
    ]
    assert sync_result.exit_code == 0
    assert json.loads(sync_result.stdout)["created"] == 1
    assert unknown_result.exit_code == 1
    assert "unknown Git repository" in unknown_result.stderr
    assert "Traceback" not in unknown_result.stderr


def test_cli_refreshes_all_registered_git_checkouts(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n\nRefresh fixture", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    assert registered.exit_code == 0, registered.output

    refreshed = runner.invoke(app, ["refresh", "--workspace", str(workspace)])
    unchanged = runner.invoke(app, ["refresh", "--workspace", str(workspace)])

    assert refreshed.exit_code == 0, refreshed.output
    assert json.loads(refreshed.stdout)["status"] == "changed"
    assert json.loads(refreshed.stdout)["git"][0]["created"] == 1
    assert "documents" not in json.loads(refreshed.stdout)["git"][0]
    assert json.loads(refreshed.stdout)["feishu"] == []
    assert unchanged.exit_code == 0, unchanged.output
    assert json.loads(unchanged.stdout)["status"] == "unchanged"


def test_cli_status_changeset_list_and_auto_obsidian_rebuild(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["import", str(source), "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]

    listed = runner.invoke(app, ["changeset-list", "--workspace", str(workspace)])
    assert listed.exit_code == 0, listed.output
    assert [item["changeset_id"] for item in json.loads(listed.stdout)] == [changeset_id]

    before = json.loads(runner.invoke(app, ["status", "--workspace", str(workspace)]).stdout)
    assert before["current_commit"]
    assert before["sources"] == {"current": 1, "applied": 0}
    assert before["versions"] == {"current": 1}
    assert before["applied_pages"] == 0
    assert before["changesets"]["pending"] == 1
    assert before["changesets"]["items"][0]["changeset_id"] == changeset_id
    assert before["obsidian"] == {"generated": False, "lagging": True}

    applied = review_approve_apply(runner, changeset_id, workspace)
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["obsidian"]["status"] == "built"
    assert (workspace / "obsidian" / "Home.md").is_file()

    after = json.loads(runner.invoke(app, ["status", "--workspace", str(workspace)]).stdout)
    assert after["sources"] == {"current": 1, "applied": 1}
    assert after["versions"] == {"current": 1}
    assert after["applied_pages"] == 1
    assert after["changesets"]["pending"] == 0
    assert after["obsidian"] == {"generated": True, "lagging": False}
    applied_sources = json.loads(
        runner.invoke(app, ["source-list", "--workspace", str(workspace)]).stdout
    )
    assert applied_sources[0]["is_applied"] is True
    assert applied_sources[0]["git_repository"] is None
    assert (
        json.loads(runner.invoke(app, ["changeset-list", "--workspace", str(workspace)]).stdout)
        == []
    )


def test_cli_source_list_reports_git_repository_ownership(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n\nSource list fixture", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    repository_id = json.loads(registered.stdout)["repository_id"]
    synced = runner.invoke(
        app,
        ["git-sync", repository_id, "--workspace", str(workspace)],
    )
    assert synced.exit_code == 0, synced.output

    result = runner.invoke(app, ["source-list", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    sources = json.loads(result.stdout)
    assert sources[0]["title"] == "README"
    assert sources[0]["is_current"] is True
    assert sources[0]["is_applied"] is False
    assert sources[0]["category"] == "refs"
    assert sources[0]["sensitivity"] == "local_only"
    assert sources[0]["git_repository"] == {
        "repository_id": repository_id,
        "name": "repository",
    }


def test_cli_doctor_reports_readonly_checks_and_remediation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        "memoryforge.cli.inspect_posix_namespace_lock_root",
        lambda: lock_root,
    )

    healthy = json.loads(runner.invoke(app, ["doctor", "--workspace", str(workspace)]).stdout)
    names = {check["name"] for check in healthy["checks"]}

    assert healthy["status"] == "ok"
    assert names == {
        "python",
        "platform",
        "git",
        "workspace",
        "index",
        "projection",
        "lock_directory",
        "model",
        "feishu",
    }
    assert healthy["checks"][-1]["status"] in {"configured", "not_configured"}

    def unsafe_lock_root() -> Path:
        raise OSError("lock root unavailable")

    monkeypatch.setattr(
        "memoryforge.cli.inspect_posix_namespace_lock_root",
        unsafe_lock_root,
    )
    lock_broken = json.loads(runner.invoke(app, ["doctor", "--workspace", str(workspace)]).stdout)
    lock_check = next(check for check in lock_broken["checks"] if check["name"] == "lock_directory")
    assert lock_check["status"] == "error"
    assert "0700" in lock_check["remediation"]

    broken = json.loads(
        runner.invoke(app, ["doctor", "--workspace", str(tmp_path / "missing")]).stdout
    )
    workspace_check = next(check for check in broken["checks"] if check["name"] == "workspace")
    assert workspace_check["status"] == "error"
    assert workspace_check["remediation"]
    assert any("memoryforge init" in remediation for remediation in broken["remediation"])


def test_cli_doctor_detects_sqlite_and_projection_corruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nGrounded fact.\n", encoding="utf-8")
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported.exit_code == 0
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0
    assert (
        review_approve_apply(
            runner,
            json.loads(staged.stdout)["changeset_id"],
            workspace,
        ).exit_code
        == 0
    )

    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        connection.execute("DELETE FROM page_sources")
    projection = json.loads(runner.invoke(app, ["doctor", "--workspace", str(workspace)]).stdout)
    projection_check = next(
        check for check in projection["checks"] if check["name"] == "projection"
    )
    assert projection["status"] == "error"
    assert projection_check["status"] == "error"

    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO page_sources(page_path, source_id) VALUES (?, ?)",
            ("wiki/pages/orphan.md", "0" * 64),
        )
    corrupted = json.loads(runner.invoke(app, ["doctor", "--workspace", str(workspace)]).stdout)
    index_check = next(check for check in corrupted["checks"] if check["name"] == "index")
    assert index_check["status"] == "error"
    assert "foreign_key_check" in index_check["message"]


def test_cli_watch_once_refreshes_and_stages_wiki_update(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text(
        "# Service\n\nWatch compiles this committed documentation.",
        encoding="utf-8",
    )
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    assert registered.exit_code == 0, registered.output

    watched = runner.invoke(app, ["watch", "--once", "--workspace", str(workspace)])

    assert watched.exit_code == 0, watched.output
    payload = json.loads(watched.stdout)
    assert payload["status"] == "proposed"
    assert payload["git"][0]["created"] == 1
    assert payload["changeset"]["status"] == "PROPOSED"
    assert payload["changeset"]["files"]


def test_cli_watch_stages_docs_and_multiple_code_wiki_changesets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    repositories = []
    for name, source in (
        (
            "repository-a",
            "def alpha(value: str) -> str:\n    return value.upper()\n",
        ),
        (
            "repository-b",
            "def beta(value: str) -> str:\n    return value.lower()\n",
        ),
    ):
        checkout = tmp_path / name
        checkout.mkdir()
        _git(checkout, "init")
        _git(checkout, "config", "user.email", "test@example.com")
        _git(checkout, "config", "user.name", "Test User")
        (checkout / "README.md").write_text("# Service\n", encoding="utf-8")
        code_path = checkout / "src" / "service.py"
        code_path.parent.mkdir()
        code_path.write_text(source, encoding="utf-8")
        _commit_all(checkout, "Add service")

        registered = runner.invoke(
            app,
            ["git-add", str(checkout), "--workspace", str(workspace)],
        )
        assert registered.exit_code == 0, registered.output
        repository_id = json.loads(registered.stdout)["repository_id"]
        synced = runner.invoke(
            app,
            ["git-sync", repository_id, "--workspace", str(workspace)],
        )
        assert synced.exit_code == 0, synced.output
        repositories.append((checkout, repository_id))

    staged_docs = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged_docs.exit_code == 0, staged_docs.output
    applied_docs = review_approve_apply(
        runner, json.loads(staged_docs.stdout)["changeset_id"], workspace
    )
    assert applied_docs.exit_code == 0, applied_docs.output

    for _checkout, repository_id in repositories:
        selected = runner.invoke(
            app,
            ["code-add", repository_id, "src", "--workspace", str(workspace)],
        )
        assert selected.exit_code == 0, selected.output
        synced = runner.invoke(
            app,
            ["git-sync", repository_id, "--workspace", str(workspace)],
        )
        assert synced.exit_code == 0, synced.output

    for _, repository_id in repositories:
        code_wiki = runner.invoke(
            app,
            ["ingest", "--code-wiki", repository_id, "--workspace", str(workspace)],
        )
        assert code_wiki.exit_code == 0, code_wiki.output
        applied_code = review_approve_apply(
            runner, json.loads(code_wiki.stdout)["changeset_id"], workspace
        )
        assert applied_code.exit_code == 0, applied_code.output

    checkout_a, _ = repositories[0]
    checkout_b, _ = repositories[1]
    (checkout_a / "README.md").write_text(
        "# Service\n\nUpdated documentation.\n",
        encoding="utf-8",
    )
    (checkout_a / "src" / "service.py").write_text(
        "def alpha(value: str) -> str:\n    return value.lower()\n",
        encoding="utf-8",
    )
    (checkout_b / "src" / "service.py").write_text(
        "def beta(value: str) -> str:\n    return value.upper()\n",
        encoding="utf-8",
    )
    _commit_all(checkout_a, "Update alpha and docs")
    _commit_all(checkout_b, "Update beta")

    watched = runner.invoke(app, ["watch", "--once", "--workspace", str(workspace)])

    assert watched.exit_code == 0, watched.output
    payload = json.loads(watched.stdout)
    assert payload["status"] == "proposed"
    changesets = payload["changesets"]
    assert len(changesets) == 3
    assert len({changeset["changeset_id"] for changeset in changesets}) == 3
    assert (
        sum(
            any(path.startswith("wiki/pages/code/") for path in changeset["files"])
            for changeset in changesets
        )
        == 2
    )
    assert (
        sum(
            not any(path.startswith("wiki/pages/code/") for path in changeset["files"])
            for changeset in changesets
        )
        == 1
    )
    assert payload["changeset"] == changesets[0]

    replayed = runner.invoke(app, ["watch", "--once", "--workspace", str(workspace)])
    assert replayed.exit_code == 0, replayed.output
    unchanged = json.loads(replayed.stdout)
    assert unchanged["status"] == "unchanged", unchanged
    assert "changeset" not in unchanged
    assert "changesets" not in unchanged


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=checkout, check=True, capture_output=True, text=True)


def _commit_all(checkout: Path, message: str) -> None:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", message)
