"""Phase 1 tests: protocol-agnostic Agent Access functions.

Covers ``resolve_repository_scope`` (longest-ancestor binding, fail closed),
``query_context`` (L2 budgets, repository isolation, sensitivity gate before
Support), ``read_applied_evidence`` (applied/not-applied/citation gates and
the 2,000-char cap) and ``recall_context`` (shared with the CLI, repository
and public-only filters). All scenarios run against real temporary Workspaces
with no MCP server attached, proving the business contracts standalone.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.agent_access import (
    query_context,
    read_applied_evidence,
    recall_context,
    resolve_repository_scope,
)
from memoryforge.cli import app
from memoryforge.errors import UnmappedProjectError
from memoryforge.query import answer_question
from tests.cli_helpers import review_approve_apply

CACHE_POLICY = "# Cache policy\n\nCache entries expire after sixty seconds.\n"
RETRY_POLICY = "# Retry policy\n\nRetries stop after three attempts.\n"


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_checkout(root: Path, name: str, files: dict[str, str]) -> Path:
    checkout = root / name
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    for path, content in files.items():
        (checkout / path).write_text(content, encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    return checkout


def _bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str],
    *,
    public: bool = False,
) -> tuple[Path, Path, str]:
    """Register one checkout, sync it and apply its Wiki pages."""
    checkout = _make_checkout(tmp_path, "repository", files)
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    arguments = ["git-add", str(checkout)]
    if public:
        arguments.append("--public")
    arguments.extend(["--workspace", str(workspace)])
    registered = runner.invoke(app, arguments)
    assert registered.exit_code == 0, registered.output
    repository_id = json.loads(registered.stdout)["repository_id"]
    synced = runner.invoke(
        app,
        ["git-sync", repository_id, "--workspace", str(workspace)],
    )
    assert synced.exit_code == 0, synced.output
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    applied = review_approve_apply(runner, json.loads(proposal.stdout)["changeset_id"], workspace)
    assert applied.exit_code == 0, applied.output
    return workspace, checkout, repository_id


def _conversation_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: list[tuple[str, str, bool]],
) -> tuple[Path, list[str]]:
    """Import applied conversation memories; return workspace and source ids."""
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    source_ids: list[str] = []
    for title, answer, public in entries:
        path = tmp_path / f"{title}.md"
        path.write_text(
            f"# {title}\n\n## User\n\nWhat happened?\n\n"
            f"## Assistant (unverified)\n\n{answer}\n",
            encoding="utf-8",
        )
        arguments = [
            "import",
            str(path),
            "--category",
            "notes",
            "--tag",
            "conversation",
            "--tag",
            "platform:codex",
            "--tag",
            "unverified",
            "--workspace",
            str(workspace),
        ]
        if public:
            arguments.append("--public")
        imported = runner.invoke(app, arguments)
        assert imported.exit_code == 0, imported.output
        source_ids.append(str(json.loads(imported.stdout)["source_id"]))
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    applied = review_approve_apply(runner, json.loads(proposal.stdout)["changeset_id"], workspace)
    assert applied.exit_code == 0, applied.output
    return workspace, source_ids


def test_resolve_repository_scope_picks_the_longest_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer = _make_checkout(tmp_path, "outer", {"README.md": "# Outer\n"})
    inner = _make_checkout(outer, "inner", {"README.md": "# Inner\n"})
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    for checkout in (outer, inner):
        registered = runner.invoke(
            app,
            ["git-add", str(checkout), "--workspace", str(workspace)],
        )
        assert registered.exit_code == 0, registered.output
    inner_project = inner / "src" / "pkg"
    inner_project.mkdir(parents=True)
    (outer / "docs").mkdir()

    resolved = resolve_repository_scope(workspace, inner_project)
    assert resolved.checkout_path == str(inner)
    assert resolved.repository_id != resolve_repository_scope(workspace, outer).repository_id
    assert resolve_repository_scope(workspace, outer / "docs").checkout_path == str(outer)


def test_resolve_repository_scope_fails_closed_for_unmapped_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _make_checkout(tmp_path, "repository", {"README.md": "# Service\n"})
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    ).exit_code == 0

    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()
    with pytest.raises(UnmappedProjectError):
        resolve_repository_scope(workspace, unregistered)


def test_resolve_repository_scope_rejects_a_non_directory_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    with pytest.raises(ValueError, match="existing directory"):
        resolve_repository_scope(workspace, tmp_path / "missing")


def test_query_context_answers_bounded_context_from_bound_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, repository_id = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = query_context(workspace, checkout, "When do cache entries expire?")

    assert result["status"] == "answered"
    assert result["repository"]["repository_id"] == repository_id
    assert result["repository"]["name"] == "repository"
    assert "Cache entries expire after sixty seconds." in str(result["answer_hint"])
    assert len(result["workspace_commit"]) == 40
    assert len(result["wiki_pages"]) <= 3
    assert len(result["citations"]) <= 6
    citation = result["citations"][0]
    assert citation["wiki_page"].startswith("wiki/pages/")
    assert citation["section"]
    assert citation["display_source"]
    assert str(repository_id) not in str(citation["display_source"])
    budget = result["budget"]
    assert budget["max_pages"] == 3
    assert budget["max_citations"] == 6
    assert budget["max_output_characters"] == 8000
    assert budget["output_characters"] <= 8000
    assert budget["truncated"] is False


def test_query_context_clamps_pages_and_citations_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = query_context(
        workspace,
        checkout,
        "When do cache entries expire?",
        max_pages=9,
        max_citations=99,
    )

    assert result["budget"]["max_pages"] == 3
    assert result["budget"]["max_citations"] == 6


def test_query_context_returns_unmapped_project_for_unknown_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = query_context(workspace, tmp_path / "unregistered", "When do cache entries expire?")

    assert result["status"] == "unmapped_project"
    assert "answer_hint" not in result


def test_query_context_returns_workspace_unavailable_for_broken_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = query_context(tmp_path / "missing", checkout, "When do cache entries expire?")

    assert result["status"] == "workspace_unavailable"


def test_query_context_filters_local_only_evidence_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=False,
    )

    denied = query_context(workspace, checkout, "When do cache entries expire?")
    authorized = query_context(
        workspace,
        checkout,
        "When do cache entries expire?",
        allow_local=True,
    )

    assert denied["status"] == "unknown"
    assert denied["answer_hint"] == ""
    assert denied["citations"] == []
    assert authorized["status"] == "answered"


def test_answer_question_public_only_gates_the_non_llm_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=False,
    )

    legacy = answer_question(workspace, "When do cache entries expire?")
    gated = answer_question(
        workspace,
        "When do cache entries expire?",
        public_only=True,
    )

    assert legacy["status"] == "answered"
    assert gated["status"] == "unknown"
    assert gated["answer"] == "不知道"


def test_query_context_isolates_repositories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_a = _make_checkout(tmp_path, "repo-a", {"README.md": CACHE_POLICY})
    checkout_b = _make_checkout(tmp_path, "repo-b", {"README.md": RETRY_POLICY})
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    repository_ids: dict[str, str] = {}
    for checkout in (checkout_a, checkout_b):
        registered = runner.invoke(
            app,
            ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
        )
        assert registered.exit_code == 0, registered.output
        repository_ids[str(checkout)] = json.loads(registered.stdout)["repository_id"]
        synced = runner.invoke(
            app,
            ["git-sync", repository_ids[str(checkout)], "--workspace", str(workspace)],
        )
        assert synced.exit_code == 0, synced.output
        proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
        assert proposal.exit_code == 0, proposal.output
        applied = review_approve_apply(
            runner,
            json.loads(proposal.stdout)["changeset_id"],
            workspace,
        )
        assert applied.exit_code == 0, applied.output

    scoped_a = query_context(workspace, checkout_a, "When do retries stop?")
    scoped_a_own = query_context(workspace, checkout_a, "When do cache entries expire?")
    scoped_b_own = query_context(workspace, checkout_b, "When do retries stop?")

    assert scoped_a["status"] == "unknown"
    assert scoped_a_own["status"] == "answered"
    assert scoped_b_own["status"] == "answered"
    assert scoped_a_own["repository"]["repository_id"] == repository_ids[str(checkout_a)]
    assert scoped_b_own["repository"]["repository_id"] == repository_ids[str(checkout_b)]


def test_query_context_truncates_output_to_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_paragraph = ("Cache entries expire after sixty seconds. " * 800).strip()
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": f"# Cache policy\n\n{long_paragraph}\n"},
        public=True,
    )

    result = query_context(workspace, checkout, "When do cache entries expire?")

    assert result["budget"]["truncated"] is True
    assert result["budget"]["output_characters"] <= 8000
    assert result["status"] == "answered"


def test_read_applied_evidence_reads_one_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, repository_id = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )
    context = query_context(workspace, checkout, "When do cache entries expire?")
    citation = context["citations"][0]

    result = read_applied_evidence(
        workspace,
        checkout,
        source_id=str(citation["source_id"]),
        source_version=int(citation["source_version"]),
        locator=str(citation["locator"]),
    )

    assert result["status"] == "read"
    assert result["text"] == citation["quote"]
    assert result["characters"] == len(str(citation["quote"]))
    assert result["truncated"] is False
    assert result["locator"] == citation["locator"]
    assert str(repository_id) not in str(result["display_source"])


def test_read_applied_evidence_caps_characters_and_flags_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": f"# Cache policy\n\n{CACHE_POLICY.split(chr(10), 2)[2] * 40}\n"},
        public=True,
    )
    context = query_context(workspace, checkout, "When do cache entries expire?")
    citation = context["citations"][0]

    result = read_applied_evidence(
        workspace,
        checkout,
        source_id=str(citation["source_id"]),
        source_version=int(citation["source_version"]),
        locator=str(citation["locator"]),
        max_characters=100,
    )

    assert result["status"] == "read"
    assert result["characters"] == 100
    assert result["truncated"] is True


def test_read_applied_evidence_rejects_unknown_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = read_applied_evidence(
        workspace,
        checkout,
        source_id="a" * 64,
        source_version=1,
        locator="chars:0-5",
    )

    assert result["status"] == "citation_not_found"


def test_read_applied_evidence_denies_local_scope_unless_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=False,
    )
    context = query_context(
        workspace,
        checkout,
        "When do cache entries expire?",
        allow_local=True,
    )
    citation = context["citations"][0]

    denied = read_applied_evidence(
        workspace,
        checkout,
        source_id=str(citation["source_id"]),
        source_version=int(citation["source_version"]),
        locator=str(citation["locator"]),
    )
    authorized = read_applied_evidence(
        workspace,
        checkout,
        source_id=str(citation["source_id"]),
        source_version=int(citation["source_version"]),
        locator=str(citation["locator"]),
        allow_local=True,
    )

    assert denied["status"] == "local_scope_denied"
    assert authorized["status"] == "read"


def test_read_applied_evidence_returns_not_applied_for_stale_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _ = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )
    runner = CliRunner()
    updated = tmp_path / "cache.md"
    updated.write_text(
        "# Cache policy\n\nCache entries now expire after thirty seconds.\n",
        encoding="utf-8",
    )
    imported = runner.invoke(
        app,
        ["import", str(updated), "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    source_id = str(json.loads(imported.stdout)["source_id"])
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    with sqlite3.connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        version_id = int(
            connection.execute(
                """
                SELECT versions.id
                FROM source_versions AS versions
                JOIN sources ON sources.id = versions.source_id
                WHERE sources.source_id = ?
                ORDER BY versions.id DESC LIMIT 1
                """,
                (source_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO wiki_facts (
                fact_id, page_path, source_id, source_version, locator,
                section_path, quote, routing_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "stale-fact",
                "wiki/pages/stale.md",
                source_id,
                version_id,
                "chars:0-10",
                "Verified facts",
                "stale quote",
                "",
            ),
        )

    result = read_applied_evidence(
        workspace,
        checkout,
        source_id=source_id,
        source_version=version_id,
        locator="chars:0-10",
    )

    assert result["status"] == "not_applied"


def test_recall_context_returns_conversation_memories_like_the_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [("Cleanup investigation", "Cleanup requires the finalizer.", False)],
    )

    direct = recall_context(workspace)
    runner = CliRunner()
    cli_result = runner.invoke(app, ["recall", "--workspace", str(workspace)])

    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.stdout) == direct
    assert direct["status"] == "recalled"
    assert direct["summary"] == "Cleanup requires the finalizer."
    assert "Unverified recalled conversation memory" in str(direct["startup_context"])


def test_recall_context_filters_by_repository_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, source_ids = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [
            ("Cleanup investigation", "Cleanup requires the finalizer.", False),
            ("DataFlow concurrency", "CreateDataFlow uses an idempotency key.", False),
        ],
    )
    scoped_repository_id = "b" * 64
    with sqlite3.connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        connection.execute(
            "UPDATE wiki_facts SET repository_id = ? WHERE source_id = ?",
            (scoped_repository_id, source_ids[1]),
        )

    full = recall_context(workspace)
    scoped = recall_context(workspace, repository_id=scoped_repository_id)

    assert [note["title"] for note in full["recent_memories"]] == [
        "DataFlow concurrency",
        "Cleanup investigation",
    ]
    assert [note["title"] for note in scoped["recent_memories"]] == ["DataFlow concurrency"]


def test_recall_context_filters_local_only_memories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [
            ("Cleanup investigation", "Cleanup requires the finalizer.", False),
            ("DataFlow concurrency", "CreateDataFlow uses an idempotency key.", True),
        ],
    )

    public_only = recall_context(workspace, public_only=True)

    assert [note["title"] for note in public_only["recent_memories"]] == [
        "DataFlow concurrency"
    ]


def test_recall_context_caps_startup_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [("Cleanup investigation", "Cleanup requires the finalizer.", False)],
    )

    uncapped = recall_context(workspace)
    capped = recall_context(workspace, startup_context_limit=40)

    assert len(str(capped["startup_context"])) <= len(str(uncapped["startup_context"]))
    assert str(capped["startup_context"]).endswith("… (truncated)")


def test_recall_context_is_empty_when_no_conversation_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _repository_id = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    result = recall_context(workspace)

    assert result["status"] == "empty"
    assert result["recent_memories"] == []
