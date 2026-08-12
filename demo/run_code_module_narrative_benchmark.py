#!/usr/bin/env python3
"""Run the fixed 20-case code module narrative benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.code_index import build_code_index
from memoryforge.code_models import CodeIndexSnapshot, ModuleNarrative, make_code_wiki_path
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.linting import lint_workspace
from memoryforge.module_planner import build_module_plan
from memoryforge.provider import OpenAICompatibleProvider, ProviderConfig
from memoryforge.wiki_facts import parse_page_citations
from memoryforge.workspace import (
    Workspace,
    init_workspace,
    read_source_excerpt,
    rebuild_applied_projection,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "demo/fixtures/code_wiki_project"
SUITE = REPO_ROOT / "demo/evaluation/code_module_narrative_development.json"


class NarrativeCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    category: Literal["module_responsibility", "call_flow"]
    module_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_source_paths: tuple[str, ...] = Field(min_length=1)
    required_terms: tuple[str, ...] = Field(min_length=1)


class NarrativeAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_grounding: float = Field(ge=0.0, le=1.0)
    minimum_fact_coverage: float = Field(ge=0.0, le=1.0)


class NarrativeSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    label_scope: str = Field(min_length=1)
    acceptance: NarrativeAcceptance
    cases: tuple[NarrativeCase, ...] = Field(min_length=1)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    evidence = build_evidence(workdir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote code module narrative evidence to {output}")


def build_evidence(
    workdir: Path,
    *,
    provider: OpenAICompatibleProvider | None = None,
) -> dict[str, Any]:
    suite = NarrativeSuite.model_validate_json(SUITE.read_text(encoding="utf-8"))
    source_repo = workdir / "source"
    workspace = workdir / "workspace"
    shutil.copytree(FIXTURE, source_repo)
    _git(source_repo, "init")
    _git(
        source_repo,
        "remote",
        "add",
        "origin",
        "https://example.invalid/memoryforge-narrative-fixture.git",
    )
    _git(source_repo, "config", "user.email", "benchmark@example.com")
    _git(source_repo, "config", "user.name", "MemoryForge Benchmark")
    _git(source_repo, "add", ".")
    _git(source_repo, "commit", "-m", "Narrative fixture v1")

    init_workspace(workspace)
    repository = register_git_checkout(workspace, source_repo)
    sync_git_checkout(workspace, repository.repository_id)
    for path in ("py", "go", "ts"):
        register_git_code_module(workspace, repository.repository_id, path)
    sync_git_checkout(workspace, repository.repository_id)

    snapshot = build_code_index(workspace, repository.repository_id)
    plan = build_module_plan(snapshot)
    selected_provider = provider or OpenAICompatibleProvider(ProviderConfig.from_environment())
    compilation = compile_code_wiki(
        workspace,
        snapshot,
        plan,
        provider=selected_provider,
        allow_local=True,
    )
    if compilation is None:
        raise RuntimeError("narrative fixture produced no Code Wiki compilation")
    stored = ChangeSetStore(Workspace.open(workspace)).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    runner = CliRunner()
    for command in ("review", "approve"):
        result = runner.invoke(
            app,
            [command, stored.changeset.changeset_id, "--workspace", str(workspace)],
        )
        if result.exit_code != 0:
            raise RuntimeError(f"narrative ChangeSet {command} failed: {result.output.strip()}")
    applied = runner.invoke(
        app,
        ["apply", stored.changeset.changeset_id, "--workspace", str(workspace)],
    )
    if applied.exit_code != 0:
        raise RuntimeError(f"narrative ChangeSet apply failed: {applied.output.strip()}")

    rebuild_applied_projection(Workspace.open(workspace))
    evaluation = _evaluate_narratives(
        workspace,
        repository.repository_id,
        snapshot,
        suite,
    )
    model = getattr(getattr(selected_provider, "config", None), "model", "controlled-provider")
    return {
        "schema_version": 1,
        "memoryforge_commit": _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git_output(REPO_ROOT, "status", "--porcelain")),
        "fixture": {
            "path": str(FIXTURE.relative_to(REPO_ROOT)),
            "suite_path": str(SUITE.relative_to(REPO_ROOT)),
            "suite_sha256": hashlib.sha256(SUITE.read_bytes()).hexdigest(),
        },
        "provider": {"model": model},
        "workflow": {
            "lint": lint_workspace(workspace),
            "projection_rebuild": "passed",
        },
        "evaluation": evaluation,
    }


def _evaluate_narratives(
    workspace: Path,
    repository_id: str,
    snapshot: CodeIndexSnapshot,
    suite: NarrativeSuite,
) -> dict[str, Any]:
    source_paths = {
        symbol.location.source_id: symbol.location.relative_path for symbol in snapshot.symbols
    }
    module_results: dict[str, dict[str, Any]] = {}
    grounding_checks: list[bool] = []
    case_results = []
    for case in suite.cases:
        module_result = module_results.get(case.module_path)
        if module_result is None:
            page_path = make_code_wiki_path(repository_id, case.module_path)
            content = (workspace / page_path).read_text(encoding="utf-8")
            narrative = _page_narrative(content)
            statements = (
                (
                    narrative.purpose,
                    *narrative.responsibilities,
                    *narrative.key_flows,
                )
                if narrative is not None
                else ()
            )
            statement_texts = {statement.text for statement in statements}
            citations = [
                citation
                for citation in parse_page_citations(content)
                if citation["quote"] in statement_texts
            ]
            cited_paths = {
                source_paths[citation["source_id"]]
                for citation in citations
                if citation["source_id"] in source_paths
            }
            statement_grounding = []
            for statement in statements:
                statement_citations = [
                    citation for citation in citations if citation["quote"] == statement.text
                ]
                grounded = bool(statement_citations) and all(
                    bool(
                        read_source_excerpt(
                            workspace,
                            source_id=citation["source_id"],
                            source_version=citation["source_version"],
                            locator=citation["locator"],
                        )
                    )
                    for citation in statement_citations
                )
                statement_grounding.append(grounded)
                grounding_checks.append(grounded)
            module_result = {
                "synthesis_status": "synthesized" if narrative is not None else "fallback",
                "text": " ".join(statement_texts),
                "cited_paths": cited_paths,
                "statement_grounding": statement_grounding,
            }
            module_results[case.module_path] = module_result

        text = str(module_result["text"]).casefold()
        expected_paths = set(case.expected_source_paths)
        terms_covered = all(term.casefold() in text for term in case.required_terms)
        sources_covered = expected_paths <= set(module_result["cited_paths"])
        covered = terms_covered and sources_covered
        case_results.append(
            {
                "id": case.id,
                "category": case.category,
                "question": case.question,
                "module_path": case.module_path,
                "synthesis_status": module_result["synthesis_status"],
                "terms_covered": terms_covered,
                "sources_covered": sources_covered,
                "covered": covered,
            }
        )

    fact_coverage = _ratio(result["covered"] for result in case_results)
    citation_grounding = _ratio(grounding_checks)
    return {
        "suite": suite.name,
        "case_count": len(case_results),
        "metrics": {
            "fact_coverage": fact_coverage,
            "citation_grounding": citation_grounding,
            "synthesis_success_rate": _ratio(
                result["synthesis_status"] == "synthesized" for result in module_results.values()
            ),
        },
        "gates": {
            "fact_coverage": fact_coverage >= suite.acceptance.minimum_fact_coverage,
            "citation_grounding": citation_grounding >= suite.acceptance.citation_grounding,
        },
        "cases": case_results,
    }


def _page_narrative(content: str) -> ModuleNarrative | None:
    if not content.startswith("---\n"):
        raise RuntimeError("narrative page is missing frontmatter")
    closing = content.find("\n---\n", 4)
    if closing < 0:
        raise RuntimeError("narrative page frontmatter is not closed")
    fields = {
        key.strip(): value.strip()
        for line in content[4:closing].splitlines()
        for key, separator, value in (line.partition(":"),)
        if separator
    }
    raw_narrative = fields.get("synthesis_narrative")
    if raw_narrative is None:
        if fields.get("synthesis_status") == "fallback":
            return None
        raise RuntimeError("module narrative was not synthesized")
    return ModuleNarrative.model_validate_json(raw_narrative)


def _ratio(values: Iterable[object]) -> float:
    checked = [bool(value) for value in values]
    return round(sum(checked) / len(checked), 3) if checked else 0.0


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=root,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=root,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_environment() -> dict[str, str]:
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "MemoryForge Benchmark",
        "GIT_AUTHOR_EMAIL": "benchmark@example.com",
        "GIT_COMMITTER_NAME": "MemoryForge Benchmark",
        "GIT_COMMITTER_EMAIL": "benchmark@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
