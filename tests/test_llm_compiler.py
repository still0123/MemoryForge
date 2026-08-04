from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.compiler import compile_pending_sources
from memoryforge.models import CompilationPlan, PageChange, PlannedPage
from memoryforge.workspace import Workspace


class FakeProvider:
    def __init__(self, changes: tuple[PageChange, ...]) -> None:
        self.changes = changes
        self.messages: tuple[dict[str, str], ...] | None = None

    def compile_pages(self, messages: tuple[dict[str, str], ...]) -> tuple[PageChange, ...]:
        self.messages = messages
        return self.changes


class PlanningFakeProvider(FakeProvider):
    def __init__(self, changes: tuple[PageChange, ...], plan: CompilationPlan) -> None:
        super().__init__(changes)
        self.plan = plan
        self.plan_messages: tuple[dict[str, str], ...] | None = None

    def plan_pages(self, messages: tuple[dict[str, str], ...]) -> CompilationPlan:
        self.plan_messages = messages
        return self.plan


def test_llm_compiler_merges_two_pending_sources_and_keeps_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "cache.md"
    second = repository / "storage.md"
    first_text = "# Cache\n\nCache entries expire after sixty seconds.\n"
    second_text = "# Storage\n\nStorage uses a local disk.\n"
    first.write_text(first_text, encoding="utf-8")
    second.write_text(second_text, encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_result = runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)])
    second_result = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    first_import = json.loads(first_result.stdout)
    second_import = json.loads(second_result.stdout)
    first_end = first_text.index("Cache entries") + len("Cache entries expire after sixty seconds.")
    second_end = second_text.index("Storage uses") + len("Storage uses a local disk.")

    change = PageChange(
        path="wiki/pages/cache-storage.md",
        title="Cache and storage",
        page_type="concept",
        summary="Cache and storage use local resources.",
        body="Cache and storage are local subsystems.",
        source_ids=(first_import["source_id"], second_import["source_id"]),
        citations=(
            {
                "source_id": first_import["source_id"],
                "locator": f"chars:{first_text.index('Cache entries')}-{first_end}",
            },
            {
                "source_id": second_import["source_id"],
                "locator": f"chars:{second_text.index('Storage uses')}-{second_end}",
            },
        ),
    )
    provider = FakeProvider((change,))
    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    assert compilation.changeset.source_ids == (
        first_import["source_id"],
        second_import["source_id"],
    )
    merged_path = (
        "wiki/pages/merged-"
        f"{min(first_import['source_id'], second_import['source_id'])[:8]}-"
        f"{max(first_import['source_id'], second_import['source_id'])[:8]}.md"
    )
    assert set(compilation.candidate_files) == {
        merged_path,
        "wiki/INDEX.md",
    }
    page = compilation.candidate_files[merged_path]
    assert "source_versions:" in page
    assert "## Model summary (unverified)" in page
    assert "Cache and storage are local subsystems." in page
    assert first_import["source_id"] in page
    assert second_import["source_id"] in page
    assert not page.startswith("---\n---")
    assert provider.messages is not None
    assert "CURRENT INDEX:" not in provider.messages[1]["content"]
    assert "PENDING SOURCES:" in provider.messages[1]["content"]

    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    assert stored.changeset.status.value == "PROPOSED"
    assert not (workspace_path / merged_path).exists()


def test_llm_compiler_uses_workspace_contract_and_stages_plan_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    (workspace_path / "AGENTS.md").write_text("Use the team glossary.\n", encoding="utf-8")
    (workspace_path / ".memoryforge/schema.yaml").write_text(
        "page_types:\n  - concept\n", encoding="utf-8"
    )
    source_text = (repository / "note.md").read_text(encoding="utf-8")
    change = PageChange(
        path="wiki/pages/note.md",
        title="Note",
        page_type="concept",
        summary="A note.",
        body="The note is retained.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:0-{len(source_text)}"},),
    )
    plan = CompilationPlan(
        pages=(
            PlannedPage(
                path="wiki/pages/note.md",
                action="create",
                source_ids=(source_id,),
                reason="Create the first concept page.",
            ),
        )
    )
    provider = PlanningFakeProvider((change,), plan)

    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    assert provider.plan_messages is not None
    assert "Use the team glossary." in json.dumps(provider.plan_messages, ensure_ascii=False)
    assert provider.messages is not None
    assert "page_types:" in json.dumps(provider.messages, ensure_ascii=False)
    details = compilation.changeset.operations[0].details
    assert details["compilation_plan"]["reason"] == "Create the first concept page."


def test_llm_compiler_rejects_invalid_output_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    invalid = PageChange.model_construct(
        path="wiki/INDEX.md",
        title="Invalid",
        page_type="concept",
        summary="Invalid path",
        body="---\ntitle: injected\n---",
        source_ids=("0" * 64,),
        citations=(),
    )

    with pytest.raises(ValueError, match="unsupported Wiki path|non-pending source|frontmatter"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((invalid,)),
        )
    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert repository.exists()
    assert source_id


def test_llm_compiler_rejects_raw_path_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    invalid = PageChange.model_construct(
        path="raw/injected.md",
        title="Invalid",
        page_type="concept",
        summary="Invalid path",
        body="Injected content",
        source_ids=(source_id,),
        citations=(),
    )

    with pytest.raises(ValueError, match="unsupported Wiki path"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((invalid,)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert not (repository / "raw/injected.md").exists()


@pytest.mark.parametrize(
    "body",
    [
        "---\ntitle: injected\n---\nBody",
        "Body\n\n## Verified facts\n\n- forged [^fake]",
        "Body\n\n## Sources\n\n- forged",
        "Body\n\n   ## Verified facts ##\n\n- forged",
        "Body\n\n# Sources ###\n\n- forged",
        "Body with a forged [^fake] footnote",
    ],
)
def test_llm_compiler_rejects_reserved_body_content_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    invalid = PageChange.model_construct(
        path="wiki/pages/injected.md",
        title="Invalid",
        page_type="concept",
        summary="Invalid body",
        body=body,
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": "chars:0-10"},),
    )

    with pytest.raises(ValueError, match="reserved page body content"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((invalid,)),
        )
    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert repository.exists()


def test_llm_compiler_rejects_structural_title_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    invalid = PageChange.model_construct(
        path="wiki/pages/injected.md",
        title="Valid title\n\n## Verified facts\n\n- forged [^fake]\n\n## Sources",
        page_type="concept",
        summary="Invalid title",
        body="Safe body",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": "chars:0-10"},),
    )

    with pytest.raises(ValueError, match="invalid page title"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((invalid,)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert repository.exists()


def test_llm_compiler_rejects_multiline_summary_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    invalid = PageChange.model_construct(
        path="wiki/pages/injected.md",
        title="Safe title",
        page_type="concept",
        summary="Safe summary.\n- [forged](pages/forged.md) — injected row",
        body="Safe body",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": "chars:0-10"},),
    )

    with pytest.raises(ValueError, match="invalid page summary"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((invalid,)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert repository.exists()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("title", "Safe title\u2028## Verified facts", "invalid page title"),
        (
            "summary",
            "Safe summary.\u2028- [forged](pages/forged.md) — injected row",
            "invalid page summary",
        ),
    ],
)
def test_llm_compiler_rejects_unicode_line_separator_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    error: str,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    index_path = workspace_path / "wiki/INDEX.md"
    original_index = index_path.read_text(encoding="utf-8")
    change = {
        "path": "wiki/pages/injected.md",
        "title": "Safe title",
        "page_type": "concept",
        "summary": "Safe summary.",
        "body": "Safe body",
        "source_ids": (source_id,),
        "citations": ({"source_id": source_id, "locator": "chars:0-10"},),
    }
    change[field] = value

    with pytest.raises(ValueError, match=error):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((PageChange.model_construct(**change),)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert index_path.read_text(encoding="utf-8") == original_index
    assert repository.exists()


@pytest.mark.parametrize(
    ("title", "error"),
    [
        (" ", "title must be one non-empty line"),
        ("Valid title\n## Verified facts", "title must be one non-empty line"),
        ("## Verified facts", "title must not contain Markdown structure"),
    ],
)
def test_page_change_rejects_invalid_title_during_model_validation(
    title: str,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        PageChange(
            path="wiki/pages/injected.md",
            title=title,
            page_type="concept",
            summary="Invalid title",
            body="Safe body",
            source_ids=("0" * 64,),
            citations=({"source_id": "0" * 64, "locator": "chars:0-10"},),
        )


def test_llm_compiler_rejects_omitted_pending_source_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, first_id = _workspace_with_one_source(tmp_path, monkeypatch)
    second = repository / "second.md"
    second.write_text("# Second\n\nA second fact.\n", encoding="utf-8")
    runner = CliRunner()
    imported = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    assert imported.exit_code == 0
    second_id = json.loads(imported.stdout)["source_id"]
    first_text = (repository / "note.md").read_text(encoding="utf-8")
    change = PageChange(
        path="wiki/pages/first.md",
        title="First source only",
        page_type="concept",
        summary="Only the first source is proposed.",
        body="This intentionally omits the second source.",
        source_ids=(first_id,),
        citations=({"source_id": first_id, "locator": f"chars:0-{len(first_text)}"},),
    )

    with pytest.raises(ValueError, match=second_id):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((change,)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        rows = connection.execute("SELECT source_id FROM applied_source_versions").fetchall()
    assert rows == []


def test_llm_compiler_keeps_distinct_pages_when_model_paths_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, first_id = _workspace_with_one_source(tmp_path, monkeypatch)
    second = repository / "second.md"
    second_text = "# Second\n\nA second useful fact.\n"
    second.write_text(second_text, encoding="utf-8")
    runner = CliRunner()
    imported = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    assert imported.exit_code == 0
    second_id = json.loads(imported.stdout)["source_id"]
    first_text = (repository / "note.md").read_text(encoding="utf-8")
    model_path = "wiki/pages/model-collision.md"
    changes = (
        PageChange(
            path=model_path,
            title="First",
            page_type="concept",
            summary="First source summary.",
            body="First source body.",
            source_ids=(first_id,),
            citations=({"source_id": first_id, "locator": f"chars:0-{len(first_text)}"},),
        ),
        PageChange(
            path=model_path,
            title="Second",
            page_type="concept",
            summary="Second source summary.",
            body="Second source body.",
            source_ids=(second_id,),
            citations=({"source_id": second_id, "locator": f"chars:0-{len(second_text)}"},),
        ),
    )

    compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider(changes)
    )

    assert compilation is not None
    first_path = f"wiki/pages/{first_id}.md"
    second_path = f"wiki/pages/{second_id}.md"
    assert "First source body." in compilation.candidate_files[first_path]
    assert "Second source body." in compilation.candidate_files[second_path]
    assert model_path not in compilation.candidate_files
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        compilation.changeset, compilation.candidate_files
    )
    applied = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0, applied.output
    assert (workspace_path / first_path).is_file()
    assert (workspace_path / second_path).is_file()
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        applied_ids = {
            source_id
            for (source_id,) in connection.execute("SELECT source_id FROM applied_source_versions")
        }
    assert {first_id, second_id} <= applied_ids


def test_llm_page_uses_local_citation_excerpt_after_review_and_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    source_text = (repository / "note.md").read_text(encoding="utf-8")
    fact = "A useful local fact."
    fact_start = source_text.index(fact)
    change = PageChange(
        path="wiki/pages/model-name.md",
        title="Local Fact",
        page_type="concept",
        summary="Cache capacity is infinite.",
        body="The model explains the policy here.",
        source_ids=(source_id,),
        citations=(
            {
                "source_id": source_id,
                "locator": f"chars:{fact_start}-{fact_start + len(fact)}",
            },
        ),
    )
    compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((change,))
    )
    assert compilation is not None
    page = compilation.candidate_files[f"wiki/pages/{source_id}.md"]
    facts = page.split("## Verified facts\n\n", maxsplit=1)[1].split("## Sources", maxsplit=1)[0]
    assert change.summary not in facts
    assert fact in facts
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        compilation.changeset, compilation.candidate_files
    )
    runner = CliRunner()
    applied = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0, applied.output
    result = runner.invoke(
        app,
        ["ask", "What useful local fact is documented?", "--workspace", str(workspace_path)],
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    assert payload["answer"] == fact
    assert change.summary not in payload["answer"]
    assert payload["citations"][0]["source_id"] == source_id


def test_llm_compiler_rejects_local_only_before_provider_or_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, _ = _workspace_with_one_source(
        tmp_path, monkeypatch, local_only=True
    )
    provider = FakeProvider(())

    with pytest.raises(ValueError, match="local_only"):
        compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert provider.messages is None
    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert repository.exists()


def test_llm_compiler_rejects_update_owned_by_another_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, first_id = _workspace_with_one_source(tmp_path, monkeypatch)
    runner = CliRunner()
    first_text = (repository / "note.md").read_text(encoding="utf-8")
    first_end = len(first_text)
    first_change = PageChange(
        path="wiki/pages/architecture.md",
        title="Architecture",
        page_type="concept",
        summary="The first architecture note.",
        body="The first source owns this page.",
        source_ids=(first_id,),
        citations=({"source_id": first_id, "locator": f"chars:0-{first_end}"},),
    )
    first_compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((first_change,))
    )
    assert first_compilation is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        first_compilation.changeset, first_compilation.candidate_files
    )
    applied = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0
    page_path = workspace_path / f"wiki/pages/{first_id}.md"
    original_page = page_path.read_text(encoding="utf-8")

    second = repository / "second.md"
    second.write_text("# Second\n\nA different architecture fact.\n", encoding="utf-8")
    imported = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    assert imported.exit_code == 0
    second_id = json.loads(imported.stdout)["source_id"]
    conflicting_path = workspace_path / f"wiki/pages/{second_id}.md"
    conflicting_path.write_text(original_page, encoding="utf-8")
    second_text = second.read_text(encoding="utf-8")
    change = PageChange(
        path="wiki/pages/architecture.md",
        title="Architecture",
        page_type="concept",
        summary="A replacement page.",
        body="This must not replace the existing page.",
        source_ids=(second_id,),
        citations=({"source_id": second_id, "locator": f"chars:0-{len(second_text)}"},),
    )
    with pytest.raises(ValueError, match="owned by different sources"):
        compile_pending_sources(Workspace.open(workspace_path), provider=FakeProvider((change,)))

    assert conflicting_path.read_text(encoding="utf-8") == original_page
    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    with sqlite3.connect(workspace_path / ".memoryforge/index.sqlite") as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM applied_source_versions WHERE source_id = ?", (second_id,)
            ).fetchone()
            is None
        )


def test_llm_compiler_allows_update_with_same_page_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    runner = CliRunner()
    first_text = (repository / "note.md").read_text(encoding="utf-8")
    first_change = PageChange(
        path="wiki/pages/architecture.md",
        title="Architecture",
        page_type="concept",
        summary="Initial architecture.",
        body="Initial body.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:0-{len(first_text)}"},),
    )
    first_compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((first_change,))
    )
    assert first_compilation is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        first_compilation.changeset, first_compilation.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    source = repository / "note.md"
    source.write_text("# Note\n\nAn updated local fact.\n", encoding="utf-8")
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace_path)])
    assert imported.exit_code == 0
    updated_text = source.read_text(encoding="utf-8")
    update = PageChange(
        path="wiki/pages/architecture.md",
        title="Architecture",
        page_type="concept",
        summary="Updated architecture.",
        body="Updated body.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:0-{len(updated_text)}"},),
    )
    compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((update,))
    )
    assert compilation is not None
    assert "Updated body." in compilation.candidate_files[f"wiki/pages/{source_id}.md"]


def test_llm_compiler_uses_canonical_single_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    runner = CliRunner()
    deterministic = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace_path)])
    assert deterministic.exit_code == 0
    deterministic_id = json.loads(deterministic.stdout)["changeset_id"]
    assert (
        runner.invoke(
            app,
            ["apply", deterministic_id, "--approve", "--workspace", str(workspace_path)],
        ).exit_code
        == 0
    )

    source = repository / "note.md"
    source.write_text("# Note\n\nA newer local fact.\n", encoding="utf-8")
    assert (
        runner.invoke(app, ["import", str(source), "--workspace", str(workspace_path)]).exit_code
        == 0
    )
    text = source.read_text(encoding="utf-8")
    change = PageChange(
        path="wiki/pages/model-picked-name.md",
        title="Renamed by model",
        page_type="concept",
        summary="Canonical single-source page.",
        body="The model title is preserved.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:0-{len(text)}"},),
    )

    compilation = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((change,))
    )

    assert compilation is not None
    canonical = f"wiki/pages/{source_id}.md"
    assert canonical in compilation.candidate_files
    assert "wiki/pages/model-picked-name.md" not in compilation.candidate_files
    assert "Renamed by model" in compilation.candidate_files[canonical]


def test_llm_compiler_uses_stable_canonical_merged_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "first.md"
    second = repository / "second.md"
    first.write_text("# First\n\nFirst fact.\n", encoding="utf-8")
    second.write_text("# Second\n\nSecond fact.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_result = runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)])
    second_result = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    first_id = json.loads(first_result.stdout)["source_id"]
    second_id = json.loads(second_result.stdout)["source_id"]
    source_ids = tuple(sorted((first_id, second_id)))
    merged_path = f"wiki/pages/merged-{source_ids[0][:8]}-{source_ids[1][:8]}.md"
    texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }

    def make_change(path: str, title: str) -> PageChange:
        return PageChange(
            path=path,
            title=title,
            page_type="concept",
            summary="Merged facts.",
            body=title,
            source_ids=(first_id, second_id),
            citations=tuple(
                {"source_id": source_id, "locator": f"chars:0-{len(texts[source_id])}"}
                for source_id in (first_id, second_id)
            ),
        )

    first_compilation = compile_pending_sources(
        Workspace.open(workspace_path),
        provider=FakeProvider((make_change("wiki/pages/first-name.md", "First merge"),)),
    )
    assert first_compilation is not None
    assert merged_path in first_compilation.candidate_files
    first_stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        first_compilation.changeset, first_compilation.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                first_stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    first.write_text("# First\n\nUpdated first fact.\n", encoding="utf-8")
    second.write_text("# Second\n\nUpdated second fact.\n", encoding="utf-8")
    assert (
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)]).exit_code
        == 0
    )
    texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    second_compilation = compile_pending_sources(
        Workspace.open(workspace_path),
        provider=FakeProvider((make_change("wiki/pages/another-name.md", "Second merge"),)),
    )

    assert second_compilation is not None
    assert merged_path in second_compilation.candidate_files
    assert "wiki/pages/another-name.md" not in second_compilation.candidate_files


def test_llm_compiler_routes_one_pending_source_without_source_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "first.md"
    second = repository / "second.md"
    first.write_text("# First\n\nFirst fact.\n", encoding="utf-8")
    second.write_text("# Second\n\nSecond fact.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_import = runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)])
    second_import = runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)])
    first_id = json.loads(first_import.stdout)["source_id"]
    second_id = json.loads(second_import.stdout)["source_id"]
    source_ids = tuple(sorted((first_id, second_id)))
    merged_path = f"wiki/pages/merged-{source_ids[0][:8]}-{source_ids[1][:8]}.md"

    def change_for(texts: dict[str, str], body: str) -> PageChange:
        return PageChange(
            path="wiki/pages/model-name.md",
            title="Combined architecture",
            page_type="concept",
            summary="Two related facts.",
            body=body,
            source_ids=source_ids,
            citations=tuple(
                {"source_id": source_id, "locator": f"chars:0-{len(texts[source_id])}"}
                for source_id in source_ids
            ),
        )

    original_texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    initial = compile_pending_sources(
        Workspace.open(workspace_path),
        provider=FakeProvider((change_for(original_texts, "Initial combined page."),)),
    )
    assert initial is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        initial.changeset, initial.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    first.write_text("# First\n\nUpdated first fact.\n", encoding="utf-8")
    updated = runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)])
    assert updated.exit_code == 0
    updated_texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    provider = FakeProvider((change_for(updated_texts, "Updated combined page."),))

    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    assert compilation.changeset.source_ids == source_ids
    assert merged_path in compilation.candidate_files
    assert f"wiki/pages/{first_id}.md" not in compilation.candidate_files
    assert "Updated combined page." in compilation.candidate_files[merged_path]
    assert provider.messages is not None
    prompt = json.dumps(provider.messages, ensure_ascii=False)
    assert "Updated first fact." in prompt
    assert "Second fact." in prompt


def test_llm_compiler_routes_mixed_pending_sources_to_existing_and_new_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "first.md"
    second = repository / "second.md"
    first.write_text("# First\n\nFirst fact.\n", encoding="utf-8")
    second.write_text("# Second\n\nSecond fact.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_id = json.loads(
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    second_id = json.loads(
        runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    merged_ids = tuple(sorted((first_id, second_id)))
    merged_path = f"wiki/pages/merged-{merged_ids[0][:8]}-{merged_ids[1][:8]}.md"

    def change_for(
        source_ids: tuple[str, ...],
        texts: dict[str, str],
        title: str,
        body: str,
    ) -> PageChange:
        return PageChange(
            path="wiki/pages/model-name.md",
            title=title,
            page_type="concept",
            summary=f"{title} summary.",
            body=body,
            source_ids=source_ids,
            citations=tuple(
                {"source_id": source_id, "locator": f"chars:0-{len(texts[source_id])}"}
                for source_id in source_ids
            ),
        )

    original_texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    initial = compile_pending_sources(
        Workspace.open(workspace_path),
        provider=FakeProvider(
            (change_for(merged_ids, original_texts, "First and second", "Initial page."),)
        ),
    )
    assert initial is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        initial.changeset, initial.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    first.write_text("# First\n\nUpdated first fact.\n", encoding="utf-8")
    assert (
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).exit_code
        == 0
    )
    third = repository / "third.md"
    third.write_text("# Third\n\nThird fact.\n", encoding="utf-8")
    third_id = json.loads(
        runner.invoke(app, ["import", str(third), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    current_texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
        third_id: third.read_text(encoding="utf-8"),
    }
    provider = FakeProvider(
        (
            change_for(merged_ids, current_texts, "First and second", "Updated page."),
            change_for((third_id,), current_texts, "Third", "New page."),
        )
    )

    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    assert set(compilation.changeset.source_ids) == {first_id, second_id, third_id}
    assert set(compilation.candidate_files) == {
        merged_path,
        f"wiki/pages/{third_id}.md",
        "wiki/INDEX.md",
    }
    assert "Updated page." in compilation.candidate_files[merged_path]
    assert "New page." in compilation.candidate_files[f"wiki/pages/{third_id}.md"]
    assert provider.messages is not None
    prompt = json.dumps(provider.messages, ensure_ascii=False)
    assert "Updated first fact." in prompt
    assert "Second fact." in prompt
    assert "Third fact." in prompt


def test_deterministic_compiler_routes_mixed_pending_sources_to_existing_and_new_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "first.md"
    second = repository / "second.md"
    first.write_text("# First\n\nFirst fact.\n", encoding="utf-8")
    second.write_text("# Second\n\nSecond fact.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_id = json.loads(
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    second_id = json.loads(
        runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    merged_ids = tuple(sorted((first_id, second_id)))
    merged_path = f"wiki/pages/merged-{merged_ids[0][:8]}-{merged_ids[1][:8]}.md"
    original_texts = {
        first_id: first.read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    initial_change = PageChange(
        path="wiki/pages/model-name.md",
        title="First and second",
        page_type="concept",
        summary="First and second summary.",
        body="Initial combined page.",
        source_ids=merged_ids,
        citations=tuple(
            {"source_id": source_id, "locator": f"chars:0-{len(original_texts[source_id])}"}
            for source_id in merged_ids
        ),
    )
    initial = compile_pending_sources(
        Workspace.open(workspace_path),
        provider=FakeProvider((initial_change,)),
    )
    assert initial is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        initial.changeset, initial.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    first.write_text("# First\n\nUpdated first fact.\n", encoding="utf-8")
    assert (
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).exit_code
        == 0
    )
    third = repository / "third.md"
    third.write_text("# Third\n\nThird fact.\n", encoding="utf-8")
    third_id = json.loads(
        runner.invoke(app, ["import", str(third), "--workspace", str(workspace_path)]).stdout
    )["source_id"]

    staged = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace_path)],
    )
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    staged_changeset = ChangeSetStore(Workspace.open(workspace_path)).get(changeset_id)
    assert set(staged_changeset.changeset.source_ids) == {first_id, second_id, third_id}
    assert set(staged_changeset.candidate_files) == {
        merged_path,
        f"wiki/pages/{third_id}.md",
        "wiki/INDEX.md",
    }
    merged_page = staged_changeset.candidate_files[merged_path]
    assert "Updated first fact." in merged_page
    assert "Second fact." in merged_page
    assert "Third fact." in staged_changeset.candidate_files[f"wiki/pages/{third_id}.md"]

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0, applied.output
    opened = Workspace.open(workspace_path)
    assert opened.page_paths_for_source(first_id) == (merged_path,)
    assert opened.page_paths_for_source(second_id) == (merged_path,)
    assert opened.page_paths_for_source(third_id) == (f"wiki/pages/{third_id}.md",)
    assert not (workspace_path / f"wiki/pages/{first_id}.md").exists()


def test_llm_compiler_can_extend_a_related_existing_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    first = repository / "cache-design.md"
    second = repository / "cache-operations.md"
    first_text = "# Cache Design\n\nCache entries expire after sixty seconds.\n"
    second_text = "# Cache Operations\n\nCache invalidation removes stale entries.\n"
    first.write_text(first_text, encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    first_id = json.loads(
        runner.invoke(app, ["import", str(first), "--workspace", str(workspace_path)]).stdout
    )["source_id"]

    initial_change = PageChange(
        path="wiki/pages/cache.md",
        title="Cache",
        page_type="concept",
        summary="Cache entries expire after sixty seconds.",
        body="Cache design notes.",
        source_ids=(first_id,),
        citations=({"source_id": first_id, "locator": f"chars:0-{len(first_text)}"},),
    )
    initial = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((initial_change,))
    )
    assert initial is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        initial.changeset, initial.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )

    second.write_text(second_text, encoding="utf-8")
    second_id = json.loads(
        runner.invoke(app, ["import", str(second), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    expanded_change = PageChange(
        path="wiki/pages/model-name.md",
        title="Cache",
        page_type="concept",
        summary="Cache covers expiry and invalidation.",
        body="Cache design now also documents invalidation.",
        source_ids=(first_id, second_id),
        citations=(
            {"source_id": first_id, "locator": f"chars:0-{len(first_text)}"},
            {"source_id": second_id, "locator": f"chars:0-{len(second_text)}"},
        ),
    )
    provider = FakeProvider((expanded_change,))
    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    original_path = f"wiki/pages/{first_id}.md"
    assert set(compilation.candidate_files) == {original_path, "wiki/INDEX.md"}
    assert compilation.changeset.source_ids == (first_id, second_id)
    assert f'sources: ["{first_id}", "{second_id}"]' in compilation.candidate_files[original_path]
    assert provider.messages is not None
    prompt = provider.messages[1]["content"]
    assert "EXISTING PAGE CARDS:" in prompt
    assert f"PATH: {original_path}" in prompt
    assert first_text not in prompt


def test_llm_compiler_rejects_duplicate_source_ownership_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, first_id = _workspace_with_one_source(tmp_path, monkeypatch)
    second = repository / "second.md"
    second.write_text("# Second\n\nSecond fact.\n", encoding="utf-8")
    second_id = json.loads(
        CliRunner().invoke(app, ["import", str(second), "--workspace", str(workspace_path)]).stdout
    )["source_id"]
    texts = {
        first_id: (repository / "note.md").read_text(encoding="utf-8"),
        second_id: second.read_text(encoding="utf-8"),
    }
    first_page = PageChange(
        path="wiki/pages/first.md",
        title="First",
        page_type="concept",
        summary="First page.",
        body="First body.",
        source_ids=(first_id,),
        citations=({"source_id": first_id, "locator": f"chars:0-{len(texts[first_id])}"},),
    )
    duplicate_page = PageChange(
        path="wiki/pages/combined.md",
        title="Combined",
        page_type="concept",
        summary="Combined page.",
        body="Combined body.",
        source_ids=(first_id, second_id),
        citations=tuple(
            {"source_id": source_id, "locator": f"chars:0-{len(texts[source_id])}"}
            for source_id in (first_id, second_id)
        ),
    )

    with pytest.raises(ValueError, match="assigned a source to multiple Wiki pages"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            provider=FakeProvider((first_page, duplicate_page)),
        )

    assert not (workspace_path / ".memoryforge/staging/proposed").exists()
    assert not (workspace_path / f"wiki/pages/{first_id}.md").exists()


def test_llm_compiler_rejects_routed_page_ownership_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, workspace_path, source_id = _workspace_with_one_source(tmp_path, monkeypatch)
    runner = CliRunner()
    text = (repository / "note.md").read_text(encoding="utf-8")
    first_change = PageChange(
        path="wiki/pages/model-name.md",
        title="Note",
        page_type="concept",
        summary="Initial note.",
        body="Initial body.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:0-{len(text)}"},),
    )
    initial = compile_pending_sources(
        Workspace.open(workspace_path), provider=FakeProvider((first_change,))
    )
    assert initial is not None
    stored = ChangeSetStore(Workspace.open(workspace_path)).create(
        initial.changeset, initial.candidate_files
    )
    assert (
        runner.invoke(
            app,
            [
                "apply",
                stored.changeset.changeset_id,
                "--approve",
                "--workspace",
                str(workspace_path),
            ],
        ).exit_code
        == 0
    )
    source = repository / "note.md"
    source.write_text("# Note\n\nUpdated fact.\n", encoding="utf-8")
    updated = runner.invoke(app, ["import", str(source), "--workspace", str(workspace_path)])
    assert updated.exit_code == 0

    with pytest.raises(ValueError, match="exactly its existing sources"):
        compile_pending_sources(
            Workspace.open(workspace_path),
            source_ids=(source_id,),
            provider=FakeProvider(()),
        )


def test_llm_prompt_does_not_include_applied_local_only_index_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    local_note = repository / "private.md"
    public_note = repository / "public.md"
    local_note.write_text("# Private Architecture Secret\n\nPrivate summary.\n", encoding="utf-8")
    public_note.write_text("# Public Architecture\n\nPublic summary.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace_path = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace_path)]).exit_code == 0
    local_result = runner.invoke(
        app,
        ["import", str(local_note), "--local-only", "--workspace", str(workspace_path)],
    )
    public_result = runner.invoke(
        app, ["import", str(public_note), "--workspace", str(workspace_path)]
    )
    assert local_result.exit_code == 0
    assert public_result.exit_code == 0

    local_id = json.loads(local_result.stdout)["source_id"]
    deterministic = runner.invoke(
        app,
        [
            "ingest",
            "--pending",
            "--source",
            local_id,
            "--workspace",
            str(workspace_path),
        ],
    )
    assert deterministic.exit_code == 0
    changeset_id = json.loads(deterministic.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace_path)],
    )
    assert applied.exit_code == 0

    public_id = json.loads(public_result.stdout)["source_id"]
    public_text = public_note.read_text(encoding="utf-8")
    end = public_text.index("Public summary.") + len("Public summary.")
    change = PageChange(
        path="wiki/pages/public.md",
        title="Public Architecture",
        page_type="concept",
        summary="Public summary.",
        body="Public architecture is documented.",
        source_ids=(public_id,),
        citations=({"source_id": public_id, "locator": f"chars:0-{end}"},),
    )
    provider = FakeProvider((change,))
    compilation = compile_pending_sources(Workspace.open(workspace_path), provider=provider)

    assert compilation is not None
    assert compilation.changeset.status.value == "PROPOSED"
    assert provider.messages is not None
    prompt = json.dumps(provider.messages, ensure_ascii=False)
    assert "Private Architecture Secret" not in prompt
    assert "Private summary." not in prompt
    assert "Public Architecture" in prompt


def test_deterministic_compiler_does_not_call_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace_path, _ = _workspace_with_one_source(tmp_path, monkeypatch)

    class ExplodingProvider:
        def compile_pages(self, _messages: Any) -> tuple[PageChange, ...]:
            raise AssertionError("deterministic ingest must not call a provider")

    compilation = compile_pending_sources(Workspace.open(workspace_path))

    assert compilation is not None


def test_llm_cli_fails_safely_when_provider_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace_path, _ = _workspace_with_one_source(tmp_path, monkeypatch)
    for name in ("MEMORYFORGE_API_BASE", "MEMORYFORGE_API_KEY", "MEMORYFORGE_MODEL"):
        monkeypatch.delenv(name, raising=False)

    result = CliRunner().invoke(
        app,
        ["ingest", "--pending", "--llm", "--workspace", str(workspace_path)],
    )

    assert result.exit_code != 0
    assert "missing provider environment variable" in (result.stdout + result.stderr)


def _workspace_with_one_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_only: bool = False,
) -> tuple[Path, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text("# Note\n\nA useful local fact.\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    runner = CliRunner()
    workspace = repository / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    import_args = ["import", str(source), "--workspace", str(workspace)]
    if local_only:
        import_args.append("--local-only")
    imported = runner.invoke(app, import_args)
    assert imported.exit_code == 0
    return repository, workspace, json.loads(imported.stdout)["source_id"]
