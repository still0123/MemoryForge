from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.interface.cli as cli_module
from memoryforge.code.code_index import build_code_index
from memoryforge.code.code_models import CitedStatement, ModuleNarrative, make_code_wiki_path
from memoryforge.compiler.code_wiki_compiler import (
    CodeWikiCompilationError,
    compile_code_wiki,
)
from memoryforge.compiler.compiler import _render_index
from memoryforge.compiler.linting import lint_workspace
from memoryforge.compiler.module_planner import build_architecture_graph, build_module_plan
from memoryforge.evaluation.evaluation import run_evaluation
from memoryforge.interface.cli import app
from memoryforge.query.provider import ProviderUnavailableError
from memoryforge.query.query import _page_citations
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import (
    Workspace,
    init_workspace,
    is_applied_source_version,
    list_current_git_source_versions,
    rebuild_applied_projection,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
    validate_candidate_page_evidence,
)
from tests.cli_helpers import review_approve_apply

SERVICE_SOURCE = """def helper(name: str) -> str:
    return f"Hello {name}"


class Service:
    def greet(self, name: str) -> str:
        return helper(name)


def test_greet() -> str:
    return Service().greet("test")
"""

NESTED_SOURCES = {
    "src/a/service.py": "def alpha(value: str) -> str:\n    return value.lower()\n",
    "src/b/service.py": "def beta(value: str) -> str:\n    return value.upper()\n",
}


class _NarrativeProvider:
    def __init__(
        self,
        *,
        citation_index: int = 0,
        error: ValueError | None = None,
    ) -> None:
        self.citation_index = citation_index
        self.error = error
        self.calls: list[dict[str, object]] = []

    def summarize_code_module(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> ModuleNarrative:
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        path = payload["module"]["path"]
        citation_indexes = (self.citation_index,)
        return ModuleNarrative(
            purpose=CitedStatement(
                text=f"`{path}` 负责协调子模块。",
                citation_indexes=citation_indexes,
            ),
            responsibilities=(
                CitedStatement(
                    text=f"`{path}` 汇总入口与依赖。",
                    citation_indexes=citation_indexes,
                ),
            ),
            key_flows=(
                CitedStatement(
                    text=f"数据经 `{path}` 的入口流向子模块。",
                    citation_indexes=citation_indexes,
                ),
            ),
        )


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
    overview_page = f"wiki/pages/repository-{repository_id[:12]}.md"
    assert set(compilation.candidate_files) == {
        "wiki/INDEX.md",
        overview_page,
        src_page,
        service_page,
    }
    parent = compilation.candidate_files[src_page]
    leaf = compilation.candidate_files[service_page]
    assert "generated: code_module_overview" in parent
    assert "sources:" not in parent
    assert "[Service](src/service.md)" in parent
    assert "generated: code_wiki" in leaf
    assert "## 快速阅读" in leaf
    assert leaf.index("## 快速阅读") < leaf.index("## Module")
    assert "这是叶子模块" in leaf
    assert "src.service.test_greet" not in leaf.split("## Module", maxsplit=1)[0]
    assert "## Verified symbols" in leaf
    assert "src.service.Service.greet" in leaf
    assert "revision `" in leaf
    overview = compilation.candidate_files[overview_page]
    assert "generated: repository_overview" in overview
    assert "```mermaid\nflowchart TD\n" in overview
    assert overview.count("```mermaid") == 1
    assert "m_" in overview
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
    assert stored.candidate_files == compilation.candidate_files
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
        "checked_pages": 3,
        "issues": [],
    }
    source_id = next(iter(snapshot.source_versions))
    assert Workspace.open(workspace).page_paths_for_source(source_id) == (service_page,)
    assert compile_code_wiki(workspace, snapshot, plan, graph) is None


def test_code_wiki_redacts_sensitive_literals_from_pages_and_model_input(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/client.py": (
                "class Client:\n"
                '    def __init__(self, AccountDesc="Admin@1234", password="Password123!"):\n'
                "        self.password = password\n"
            ),
            "src/service.py": (
                'from src.client import Client\n\ndef build():\n    return Client("Admin@1234")\n'
            ),
        },
    )
    snapshot = build_code_index(workspace, repository_id)
    provider = _NarrativeProvider()

    compilation = compile_code_wiki(
        workspace,
        snapshot,
        build_module_plan(snapshot),
        provider=provider,
        allow_local=True,
    )

    assert compilation is not None
    rendered = "\n".join(compilation.candidate_files.values())
    model_input = json.dumps(provider.calls, ensure_ascii=False)
    assert "Password123!" not in rendered
    assert "Admin@1234" not in rendered
    assert "Password123!" not in model_input
    assert "Admin@1234" not in model_input
    assert "<redacted>" in rendered
    validate_candidate_page_evidence(
        Workspace.open(workspace),
        compilation.candidate_files,
    )


def test_index_redacts_sensitive_page_summaries(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    access_key = "AKLT" + "A" * 32
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "Use this page to find the relevant Wiki page before reading it.\n\n"
        "## Concepts\n\n"
        f"- [Config](pages/config.md) — settings = {{'ak': '{access_key}'}}\n",
        encoding="utf-8",
    )

    rendered = _render_index(Workspace.open(workspace), {})

    assert access_key not in rendered
    assert "<redacted>" in rendered


def test_code_module_synthesis_requires_explicit_local_authorization(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    provider = _NarrativeProvider()

    with pytest.raises(CodeWikiCompilationError, match="explicit local LLM authorization"):
        compile_code_wiki(workspace, snapshot, plan, provider=provider)

    assert provider.calls == []


def test_code_module_synthesis_renders_grounded_parent_pages_only(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    provider = _NarrativeProvider()

    compilation = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=provider,
        allow_local=True,
    )

    assert compilation is not None
    parent_path = make_code_wiki_path(repository_id, "src")
    leaf_path = make_code_wiki_path(repository_id, "src/service")
    parent = compilation.candidate_files[parent_path]
    leaf = compilation.candidate_files[leaf_path]
    assert "synthesis_status: synthesized" in parent
    assert "## 模块职责" in parent
    assert "## 子模块分工" in parent
    assert "### 主要入口" in parent
    assert "## 核心流程" in parent
    assert "## 依赖关系" in parent
    assert "## 依据来源" in parent
    assert parent.index("## 模块职责") < parent.index("## 快速阅读")
    assert "synthesis_status:" not in leaf
    assert "## 模块职责" not in leaf
    assert "## 快速阅读" in leaf
    assert provider.calls[0]["module"] == {
        "title": "Src",
        "path": "src",
        "summary": "Code symbols from `src`.",
        "is_top_level_module": True,
    }
    citations = provider.calls[0]["citations"]
    assert isinstance(citations, list)
    assert citations
    assert all(len(item["source_excerpt"]) <= 600 for item in citations)
    assert any(
        citation["quote"] == "`src` 负责协调子模块。" for citation in _page_citations(parent)
    )

    _apply_compilation(workspace, compilation)

    rebuild_applied_projection(Workspace.open(workspace))
    assert lint_workspace(workspace)["status"] == "clean"


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(_NarrativeProvider(citation_index=999), id="unknown-citation"),
        pytest.param(
            _NarrativeProvider(error=ProviderUnavailableError("timed out")),
            id="provider-timeout",
        ),
    ],
)
def test_code_module_synthesis_falls_back_without_model_claims(
    tmp_path: Path,
    provider: _NarrativeProvider,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)

    compilation = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=provider,
        allow_local=True,
    )

    assert compilation is not None
    parent = compilation.candidate_files[make_code_wiki_path(repository_id, "src")]
    assert "synthesis_status: fallback" in parent
    assert "synthesis_reason:" in parent
    assert "自动概览" in parent
    assert "## 模块职责" not in parent
    assert "负责协调子模块" not in parent
    assert len(provider.calls) == (2 if provider.error is not None else 1)


def test_code_module_synthesis_retries_an_applied_fallback(tmp_path: Path) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    failed = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=_NarrativeProvider(error=ProviderUnavailableError("timed out")),
        allow_local=True,
    )
    assert failed is not None
    _apply_compilation(workspace, failed)
    recovered_provider = _NarrativeProvider()

    recovered = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=recovered_provider,
        allow_local=True,
    )

    assert recovered is not None
    parent = recovered.candidate_files[make_code_wiki_path(repository_id, "src")]
    assert "synthesis_status: synthesized" in parent
    assert len(recovered_provider.calls) == 1


def test_code_module_synthesis_updates_only_changed_leaf_ancestors(
    tmp_path: Path,
) -> None:
    checkout, workspace, repository_id = _synced_repository(tmp_path, NESTED_SOURCES)
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    initial_provider = _NarrativeProvider()
    initial = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=initial_provider,
        allow_local=True,
    )
    assert initial is not None
    _apply_compilation(workspace, initial)
    assert [call["module"]["path"] for call in initial_provider.calls] == [
        "src/a",
        "src/b",
        "src",
    ]
    assert [child["narrative"]["purpose"] for child in initial_provider.calls[-1]["children"]] == [
        "`src/a` 负责协调子模块。",
        "`src/b` 负责协调子模块。",
    ]

    (checkout / "src/a/service.py").write_text(
        "def alpha(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    _commit_all(checkout, "Update alpha")
    sync_git_checkout(workspace, repository_id)
    updated_snapshot = build_code_index(workspace, repository_id)
    updated_plan = build_module_plan(updated_snapshot)
    updated_provider = _NarrativeProvider()

    update = compile_code_wiki(
        workspace,
        updated_snapshot,
        updated_plan,
        provider=updated_provider,
        allow_local=True,
    )

    assert update is not None
    assert [call["module"]["path"] for call in updated_provider.calls] == [
        "src/a",
        "src",
    ]
    changed_code_pages = {
        path for path in update.candidate_files if path.startswith("wiki/pages/code/")
    }
    assert changed_code_pages == {
        make_code_wiki_path(repository_id, "src/a/service"),
        make_code_wiki_path(repository_id, "src/a"),
        make_code_wiki_path(repository_id, "src"),
    }


def test_code_module_synthesis_removes_deleted_child_references(
    tmp_path: Path,
) -> None:
    checkout, workspace, repository_id = _synced_repository(tmp_path, NESTED_SOURCES)
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    initial = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=_NarrativeProvider(),
        allow_local=True,
    )
    assert initial is not None
    _apply_compilation(workspace, initial)

    (checkout / "src/b/service.py").unlink()
    _commit_all(checkout, "Remove beta module")
    sync_git_checkout(workspace, repository_id)
    updated_snapshot = build_code_index(workspace, repository_id)
    updated_plan = build_module_plan(updated_snapshot)
    provider = _NarrativeProvider()

    update = compile_code_wiki(
        workspace,
        updated_snapshot,
        updated_plan,
        provider=provider,
        allow_local=True,
    )

    assert update is not None
    assert [call["module"]["path"] for call in provider.calls] == ["src"]
    root = update.candidate_files[make_code_wiki_path(repository_id, "src")]
    assert "src/b" not in root
    archived = {
        operation.path
        for operation in update.changeset.operations
        if operation.type.value == "ARCHIVE_PAGE"
    }
    assert archived == {
        make_code_wiki_path(repository_id, "src/b"),
        make_code_wiki_path(repository_id, "src/b/service"),
    }


def test_code_wiki_cli_requires_local_authorization_for_llm(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "--code-wiki",
            "a" * 64,
            "--llm",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "--code-wiki --llm requires --allow-local-llm" in result.output


def test_code_wiki_cli_accepts_explicitly_authorized_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {"src/service.py": SERVICE_SOURCE},
    )
    for name in ("MEMORYFORGE_API_BASE", "MEMORYFORGE_API_KEY", "MEMORYFORGE_MODEL"):
        monkeypatch.setenv(name, "configured")
    provider = _NarrativeProvider()
    monkeypatch.setattr(
        cli_module,
        "OpenAICompatibleProvider",
        lambda _config: provider,
    )

    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "--code-wiki",
            repository_id,
            "--llm",
            "--allow-local-llm",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "PROPOSED"
    assert len(provider.calls) == 1


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


def test_code_wiki_dependencies_are_queryable_and_grounded(tmp_path: Path) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/helper.ts": "export const helper = (value: string): string => value;\n",
            "src/service.ts": (
                'import { helper } from "./helper.js";\n\n'
                "export const service = (): string => helper(`a  b`);\n"
            ),
        },
    )
    _compile_and_apply(workspace, repository_id)
    service_page = workspace / make_code_wiki_path(repository_id, "src/service")
    content = service_page.read_text(encoding="utf-8")
    assert (
        '- `src.service -> src.helper` (imports): "import { helper } from \\"./helper.js\\";"'
    ) in content
    citations = _page_citations(content)
    dependencies = [citation for citation in citations if citation.get("routing_text")]
    assert {citation["quote"] for citation in dependencies} == {
        'import { helper } from "./helper.js";',
        "helper(`a  b`)",
    }
    assert not any(citation.get("is_summary", False) for citation in dependencies)

    suite = tmp_path / "relations.json"
    suite.write_text(
        json.dumps(
            {
                "name": "queryable relations",
                "cases": [
                    {
                        "id": "service-import",
                        "category": "single_hop",
                        "question": "Which module does src.service import?",
                        "expected_status": "answered",
                        "expected_source_paths": ["src/service.ts"],
                        "required_terms": ["import", "helper", "./helper.js"],
                        "repository_ids": [repository_id],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_evaluation(workspace, suite)

    assert result["memoryforge"]["answer_accuracy"] == 100.0
    assert result["memoryforge"]["citation_grounding_accuracy"] == 100.0


def test_code_wiki_uses_wider_markdown_delimiters_for_struct_tags(tmp_path: Path) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/model.go": (
                'package model\n\ntype Page struct { Offset int `json:"Offset,omitempty"` }\n'
            )
        },
    )

    snapshot = build_code_index(workspace, repository_id)
    compilation = compile_code_wiki(workspace, snapshot, build_module_plan(snapshot))

    assert compilation is not None
    page = compilation.candidate_files[make_code_wiki_path(repository_id, "src")]
    assert '``Page struct { Offset int `json:"Offset,omitempty"` }``' in page


def test_code_wiki_flattens_multiline_dependency_evidence_for_display(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/helper.py": (
                "class Helper:\n"
                "    def __init__(self, name: str, enabled: bool):\n"
                "        self.name = name\n"
            ),
            "src/service.py": (
                "from src.helper import Helper\n\n"
                "def build():\n"
                "    return Helper(\n"
                '        name="demo",\n'
                "        enabled=True,\n"
                "    )\n"
            ),
        },
    )
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    graph = build_architecture_graph(snapshot, plan)

    compilation = compile_code_wiki(workspace, snapshot, plan, graph)

    assert compilation is not None
    service_page = compilation.candidate_files[make_code_wiki_path(repository_id, "src/service")]
    dependency = next(
        line
        for line in service_page.splitlines()
        if "src.service.build -> src.helper.Helper" in line
    )
    assert "\\n" not in dependency
    assert 'Helper( name=\\"demo\\", enabled=True, )' in dependency
    citation = next(
        citation
        for citation in _page_citations(service_page)
        if "src.service.build -> src.helper.Helper" in citation.get("routing_text", "")
    )
    assert citation["quote"] == 'Helper( name="demo", enabled=True, )'


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
    result = review_approve_apply(CliRunner(), stored.changeset.changeset_id, workspace)
    assert result.exit_code == 0, result.output
    assert not legacy_page.exists()
    assert lint_workspace(workspace)["status"] == "clean"


def test_code_wiki_applies_unindexable_code_metadata_with_page_updates(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_repository(
        tmp_path,
        {
            "src/service.py": SERVICE_SOURCE,
            "src/broken.py": "def broken(:\n",
        },
    )
    snapshot = build_code_index(workspace, repository_id)
    broken = next(
        source
        for source in list_current_git_source_versions(workspace, repository_id)
        if source.relative_path == "src/broken.py"
    )
    assert broken.source_id not in snapshot.source_versions

    _compile_and_apply(workspace, repository_id)

    assert is_applied_source_version(
        workspace,
        source_id=broken.source_id,
        source_version=broken.source_version,
    )


def _compile_and_apply(workspace: Path, repository_id: str) -> None:
    snapshot = build_code_index(workspace, repository_id)
    plan = build_module_plan(snapshot)
    compilation = compile_code_wiki(workspace, snapshot, plan)
    _apply_compilation(workspace, compilation)


def _apply_compilation(workspace: Path, compilation) -> None:
    assert compilation is not None
    stored = ChangeSetStore(Workspace.open(workspace)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    result = review_approve_apply(CliRunner(), stored.changeset.changeset_id, workspace)
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
