#!/usr/bin/env python3
"""Run the frozen, model-free Agent Access evaluation (spec §16).

Builds a fresh Workspace from three synthetic fixtures (public project A,
same-name twin project B, local-only project C), applies every Wiki page,
then probes the protocol-agnostic Agent Access layer directly:
``query_context`` (L2), ``read_applied_evidence`` (L3), and
``propose_grounded_update`` (Phase 4), plus the raw ``answer_question``
baseline on the same public questions.

No provider, no paid model, no private data: the question set is fixed in
``CONTEXT_CASES`` and every metric is computed from deterministic function
results (spec §16).

Hard gates (all must hold):
  citation grounding 100%, applied-only 100%, repository isolation 100%,
  local-scope leak count 0, abstention 100%, proposal safety 100%, and
  Agent Access answer accuracy >= the raw answer_question baseline.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from memoryforge.query.agent_access import (
    propose_grounded_update,
    query_context,
    read_applied_evidence,
)
from memoryforge.query.query import answer_question
from memoryforge.storage.workspace import Workspace, _connect_readonly, is_applied_source_version

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_A = REPO_ROOT / "demo/fixtures/agent_access_project"
PROJECT_B = REPO_ROOT / "demo/fixtures/agent_access_twin"
PROJECT_C = REPO_ROOT / "demo/fixtures/agent_access_local"

# Frozen question set (spec §16 scenarios 1-9 and 11; 7, 10 and 12 run below).
CONTEXT_CASES: list[dict[str, Any]] = [
    {
        "id": "exact_symbol",
        "category": "exact code symbol",
        "question": "What is the value of cache_ttl_seconds?",
        "expected": "answered",
        "required_terms": ("sixty",),
        "repo": "a",
    },
    {
        "id": "project_concept",
        "category": "ordinary project concept",
        "question": "How long do cache entries live?",
        "expected": "answered",
        "required_terms": ("sixty",),
        "repo": "a",
    },
    {
        "id": "historical_decision",
        "category": "historical decision",
        "question": "Why was the balanced automation profile chosen?",
        "expected": "answered",
        "required_terms": ("shadow",),
        "repo": "a",
    },
    {
        "id": "multi_source",
        "category": "multi-source synthesis",
        "question": "How many retry attempts are allowed after a cache entry expires?",
        "expected": "answered",
        "required_terms": ("three",),
        "repo": "a",
        "min_sources": 2,
    },
    {
        "id": "unanswerable",
        "category": "unanswerable",
        "question": "When is the nightly archive run?",
        "expected": "unknown",
        "repo": "a",
    },
    {
        "id": "twin_isolation",
        "category": "same-name multi-repo isolation",
        "question": "How long do cache entries live in the twin project?",
        "expected": "answered",
        "required_terms": ("six hundred",),
        "repo": "b",
    },
    {
        "id": "local_denied",
        "category": "local_only unauthorized",
        "question": "What is the on-call page number?",
        "expected": "unknown",
        "repo": "c",
        "allow_local": False,
    },
    {
        "id": "local_allowed",
        "category": "local_only authorized",
        "question": "What is the on-call page number?",
        "expected": "answered",
        "required_terms": ("555-0199",),
        "repo": "c",
        "allow_local": True,
    },
]


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    evidence = build_evidence(workdir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote Agent Access evaluation evidence to {output}")
    if not evidence["gates"]["all_pass"]:
        failed = [name for name, ok in evidence["gates"]["checks"].items() if not ok]
        raise SystemExit(f"Agent Access evaluation gates failed: {', '.join(failed)}")


def build_evidence(workdir: Path) -> dict[str, Any]:
    checkouts = {
        "a": _make_checkout(workdir, "repo-a", PROJECT_A),
        "b": _make_checkout(workdir, "repo-b", PROJECT_B),
        "c": _make_checkout(workdir, "repo-c", PROJECT_C),
    }
    workspace = workdir / "workspace"
    _cli("init", str(workspace))
    repository_ids: dict[str, str] = {}
    for key, checkout in checkouts.items():
        arguments = ["git-add", str(checkout), "--workspace", str(workspace)]
        if key != "c":
            arguments.append("--public")
        registration = _cli_json(*arguments)
        repository_ids[key] = registration["repository_id"]
    for key in checkouts:
        _cli_json("git-sync", repository_ids[key], "--workspace", str(workspace))
    proposal = _cli_json("ingest", "--pending", "--workspace", str(workspace))
    _cli("review", proposal["changeset_id"], "--workspace", str(workspace))
    _cli_json("approve", proposal["changeset_id"], "--workspace", str(workspace))
    _cli_json("apply", proposal["changeset_id"], "--workspace", str(workspace))

    repo_id_to_project = {
        _repository_id_of(workspace, checkout): checkout for checkout in checkouts.values()
    }
    results = [_run_context_case(workspace, checkouts, case) for case in CONTEXT_CASES]
    # Grounding and isolation apply only to citations that were surfaced;
    # abstaining cases correctly return no citations at all.
    citation_checks = [_check_citation(case) for case in results if case["citations"]]
    applied_checks = [_check_applied(workspace, citation) for citation in _all_citations(results)]
    evidence_checks = [
        _check_evidence(workspace, repo_id_to_project, citation)
        for citation in _all_citations(results)
    ]

    # Scenario 12: one grounded proposal stages without touching the Wiki.
    proposal_check = _run_grounded_proposal(workspace, checkouts["a"], results)
    # Scenario 7: an unregistered project fails closed, never degrades.
    unmapped = _run_unmapped(workspace, workdir)
    # Scenario 10: a re-synced source version makes the old citation stale.
    stale = _run_stale_citation(workspace, checkouts, repository_ids, results)
    baseline = _run_baseline(workspace, checkouts)

    context_sizes = [case["output_characters"] for case in results]
    metrics: dict[str, Any] = {
        "answer_accuracy": _percentage(
            case["correct"] for case in results if case["expected"] == "answered"
        ),
        "abstention_accuracy": _percentage(
            case["correct"] for case in results if case["expected"] == "unknown"
        ),
        "repository_isolation": _percentage(item["isolated"] for item in citation_checks),
        "citation_grounding": _percentage(item["grounded"] for item in citation_checks),
        "applied_only_rate": _percentage(item["applied"] for item in applied_checks),
        "local_scope_leak_count": sum(case["leak"] for case in results),
        "context_average_characters": round(
            statistics.mean(context_sizes) if context_sizes else 0.0, 1
        ),
        "context_p95_characters": _percentile(context_sizes, 0.95),
        "evidence_read_rate": _percentage(item["read"] for item in evidence_checks),
        "proposal_safety_rate": _percentage(
            [proposal_check["safe"], stale["proposal_safety"]["safe"]]
        ),
    }
    checks = {
        "citation_grounding": metrics["citation_grounding"] == 1.0,
        "applied_only": metrics["applied_only_rate"] == 1.0,
        "repository_isolation": metrics["repository_isolation"] == 1.0,
        "local_scope_leak": metrics["local_scope_leak_count"] == 0,
        "abstention": metrics["abstention_accuracy"] == 1.0,
        "proposal_safety": metrics["proposal_safety_rate"] == 1.0,
        "baseline_parity": metrics["answer_accuracy"] >= baseline["accuracy"],
        "grounded_proposal_staged": proposal_check["staged"],
    }
    return {
        "suite": "agent_access_frozen",
        "question_count": len(CONTEXT_CASES) + 4,
        "scenarios": results
        + [stale["scenario"], unmapped["scenario"], proposal_check["scenario"]],
        "metrics": metrics,
        "baseline": baseline,
        "gates": {"checks": checks, "all_pass": all(checks.values())},
    }


def _run_context_case(
    workspace: Path,
    checkouts: dict[str, Path],
    case: dict[str, Any],
) -> dict[str, Any]:
    project = checkouts[case["repo"]]
    allow_local = bool(case.get("allow_local"))
    result = cast(
        dict[str, Any], query_context(workspace, project, case["question"], allow_local=allow_local)
    )
    if result.get("mode") == "map":
        selected = [
            str(entry["page_path"])
            for entry in cast(list[dict[str, object]], result.get("map", []))[:3]
        ]
        if selected:
            result = cast(
                dict[str, Any],
                query_context(
                    workspace,
                    project,
                    case["question"],
                    allow_local=allow_local,
                    page_paths=selected,
                ),
            )
    expected_repository_id = _repository_id_of(workspace, project)
    citations = result.get("citations") or []
    isolated = all(
        _citation_repository(workspace, citation) == expected_repository_id
        for citation in citations
    )
    answer = str(result.get("answer_hint", ""))
    terms_correct = all(
        term.casefold() in answer.casefold() for term in case.get("required_terms", ())
    )
    min_sources = (
        len({citation["source_id"] for citation in citations}) >= case.get("min_sources", 1)
        if citations
        else False
    )
    evidence_status = str(result.get("evidence_status", "no_local_evidence"))
    if case["expected"] == "answered":
        correct = (
            result["status"] == "ok"
            and evidence_status == "grounded"
            and terms_correct
            and isolated
            and min_sources
        )
    else:
        correct = result["status"] == "ok" and evidence_status == "no_local_evidence"
    # A local-scope leak: any citation surfaced without allow_local.
    leak = len(citations) if case["repo"] == "c" and not allow_local else 0
    return {
        "id": case["id"],
        "category": case["category"],
        "status": result["status"],
        "evidence_status": evidence_status,
        "expected": case["expected"],
        "correct": correct,
        "citations_isolated": isolated,
        "answer_hint": answer,
        "citations": [
            {
                "source_id": citation["source_id"],
                "source_version": citation["source_version"],
                "locator": citation["locator"],
                "quote": citation["quote"],
                "repository": _citation_repository(workspace, citation),
                "wiki_page": str(citation.get("wiki_page", "")),
            }
            for citation in citations
        ],
        "output_characters": int(
            cast(dict[str, object], result["budget"]).get(
                "output_characters",
                len(json.dumps(result, ensure_ascii=False)),
            )
        ),
        "leak": leak,
    }


def _check_citation(case: dict[str, Any]) -> dict[str, object]:
    # Grounded: every citation must carry a non-empty quote from the Wiki Fact.
    return {
        "isolated": case["citations_isolated"],
        "grounded": bool(case["citations"])
        and all(citation["quote"] for citation in case["citations"]),
    }


def _check_applied(workspace: Path, citation: dict[str, Any]) -> dict[str, object]:
    return {
        "applied": is_applied_source_version(
            workspace,
            source_id=citation["source_id"],
            source_version=citation["source_version"],
        )
    }


def _check_evidence(
    workspace: Path,
    repo_id_to_project: dict[str, Path],
    citation: dict[str, Any],
) -> dict[str, object]:
    project = repo_id_to_project.get(citation["repository"])
    if project is None:
        return {"read": False, "status": "unknown_repository"}
    result = cast(
        dict[str, Any],
        read_applied_evidence(
            workspace,
            project,
            source_id=citation["source_id"],
            source_version=citation["source_version"],
            locator=citation["locator"],
            allow_local=True,
        ),
    )
    text = str(result.get("text", ""))
    # Wiki Facts normalize whitespace; the raw source excerpt preserves it.
    quote = re.sub(r"\s+", " ", citation["quote"]).strip()
    normalized = re.sub(r"\s+", " ", text).strip()
    return {
        "read": result["status"] == "read" and quote in normalized,
        "status": str(result["status"]),
    }


def _run_unmapped(workspace: Path, workdir: Path) -> dict[str, Any]:
    unregistered = workdir / "unregistered"
    unregistered.mkdir(exist_ok=True)
    result = cast(
        dict[str, Any], query_context(workspace, unregistered, "How long do cache entries live?")
    )
    return {
        "scenario": {
            "id": "unmapped_project",
            "category": "unregistered project",
            "status": result["status"],
            "correct": result["status"] == "unmapped_project",
        }
    }


def _run_grounded_proposal(
    workspace: Path,
    project: Path,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    citation = _supporting_citation(results, "Retries stop after three attempts.")
    head_before = _workspace_head(workspace)
    result = cast(
        dict[str, Any],
        propose_grounded_update(
            workspace,
            project,
            question="Save the retry policy conclusion.",
            conclusion="Retries stop after three attempts.",
            citations=[_citation_payload(citation)],
            target_page=citation["wiki_page"],
        ),
    )
    changeset_id = str(result.get("changeset_id", ""))
    staged = result["status"] == "proposed" and bool(
        (workspace / ".memoryforge/staging" / changeset_id / "proposed").is_dir()
    )
    return {
        "safe": _workspace_head(workspace) == head_before,
        "staged": staged,
        "scenario": {
            "id": "grounded_proposal",
            "category": "grounded proposal",
            "status": str(result.get("status")),
            "changeset_id": changeset_id,
            "wiki_unchanged": _workspace_head(workspace) == head_before,
        },
    }


def _run_stale_citation(
    workspace: Path,
    checkouts: dict[str, Path],
    repository_ids: dict[str, str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    # The README citation: after the re-sync below its source version is no
    # longer current, so the proposal must be rejected as stale_citation.
    citation = _supporting_citation(results, "The retry policy is documented in docs/RETRY.md.")
    checkout_a = checkouts["a"]
    readme = checkout_a / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "expire after sixty seconds", "expire after sixty seconds now", 1
        ),
        encoding="utf-8",
    )
    _git(checkout_a, "add", "README.md")
    _git(checkout_a, "commit", "-m", "Bump cache TTL wording")
    _cli_json("git-sync", repository_ids["a"], "--workspace", str(workspace))
    head_before = _workspace_head(workspace)
    result = cast(
        dict[str, Any],
        propose_grounded_update(
            workspace,
            checkout_a,
            question="Save the retry-policy pointer conclusion.",
            conclusion="The retry policy is documented in docs/RETRY.md.",
            citations=[_citation_payload(citation)],
            target_page=citation["wiki_page"],
        ),
    )
    return {
        "proposal_safety": {"safe": _workspace_head(workspace) == head_before},
        "scenario": {
            "id": "stale_citation",
            "category": "expired citation",
            "status": str(result.get("status")),
            "correct": result["status"] == "stale_citation",
        },
    }


def _run_baseline(workspace: Path, checkouts: dict[str, Path]) -> dict[str, Any]:
    opened = Workspace.open_readonly(workspace)
    answerable = [case for case in CONTEXT_CASES if case["expected"] == "answered"]
    outcomes: list[dict[str, Any]] = []
    for case in answerable:
        project = checkouts[case["repo"]]
        result = cast(
            dict[str, Any],
            answer_question(
                opened.root,
                case["question"],
                provider=None,
                verify=False,
                max_pages=3,
                max_citations=6,
                allow_local=bool(case.get("allow_local")),
                public_only=not bool(case.get("allow_local")),
                repository_id=_repository_id_of(workspace, project),
            ),
        )
        answer = str(result["answer"])
        terms_correct = all(
            term.casefold() in answer.casefold() for term in case.get("required_terms", ())
        )
        outcomes.append(
            {
                "id": case["id"],
                "answered": result["status"] == "answered",
                "correct": result["status"] == "answered" and terms_correct,
            }
        )
    return {
        "engine": "answer_question(provider=None)",
        "accuracy": _percentage(item["correct"] for item in outcomes),
        "answered_rate": _percentage(item["answered"] for item in outcomes),
        "cases": outcomes,
    }


def _citation_repository(workspace: Path, citation: dict[str, Any]) -> str:
    with _connect_readonly(Workspace.open_readonly(workspace).index_path) as connection:
        row = connection.execute(
            """
            SELECT revisions.repository_id
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions ON versions.id = revisions.source_version_id
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.id = ?
            """,
            (citation["source_id"], citation["source_version"]),
        ).fetchone()
    return str(row[0]) if row else ""


def _repository_id_of(workspace: Path, project: Path) -> str:
    with _connect_readonly(Workspace.open_readonly(workspace).index_path) as connection:
        row = connection.execute(
            "SELECT repository_id FROM git_repositories WHERE checkout_path = ?",
            (str(project.resolve(strict=False)),),
        ).fetchone()
    return str(row[0]) if row else ""


def _all_citations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [citation for case in results for citation in case["citations"]]


def _supporting_citation(results: list[dict[str, Any]], phrase: str) -> dict[str, Any]:
    """Pick the cited Fact whose quote supports the proposal conclusion verbatim.

    ``answer_is_supported`` requires every conclusion clause to equal a
    segment of some cited quote, so the benchmark conclusion must be a
    verbatim segment of the evidence the L2 layer actually returned.
    """
    target = phrase.casefold()
    for case in results:
        for citation in case["citations"]:
            if target in citation["quote"].casefold():
                return citation
    raise AssertionError(f"no L2 citation supports conclusion {phrase!r}")


def _citation_payload(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": citation["source_id"],
        "source_version": citation["source_version"],
        "locator": citation["locator"],
        "quote": citation["quote"],
    }


def _workspace_head(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _percentage(values: Iterable[object]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(1 for item in items if item) / len(items), 4)


def _percentile(values: list[int], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return round(float(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]), 1)


def _make_checkout(workdir: Path, name: str, fixture: Path) -> Path:
    checkout = workdir / name
    shutil.copytree(fixture, checkout)
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "benchmark@example.com")
    _git(checkout, "config", "user.name", "MemoryForge Benchmark")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", f"{name} fixture v1")
    return checkout


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "memoryforge", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _cli_json(*arguments: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_cli(*arguments).stdout))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
