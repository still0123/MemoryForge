from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.evaluation as evaluation_module
from memoryforge.cli import app
from memoryforge.models import Sensitivity
from memoryforge.workspace import init_workspace, register_git_checkout, sync_git_checkout


def test_click_external_docs_splits_are_frozen() -> None:
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (root / "demo/evaluation/click_docs_sources_v021.json").read_text(encoding="utf-8")
    )
    suites = [
        evaluation_module.EvaluationSuite.model_validate_json(
            (root / path).read_text(encoding="utf-8")
        )
        for path in manifest["splits"].values()
    ]
    cases = [case for suite in suites for case in suite.cases]

    assert [len(suite.cases) for suite in suites] == [10, 10]
    assert len({case.id for case in cases}) == 20
    assert {case.category for case in cases} == {
        "single_hop",
        "multi_source",
        "unanswerable",
        "paraphrase",
    }
    assert manifest["repository"]["expected_document_count"] == 38
    assert manifest["label_scope"] == (
        "manually_verified_source_expectations_frozen_before_evaluation"
    )


def test_eval_compares_wiki_answers_with_raw_fts(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    workspace = repository / "workspace"
    config = repository / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "cache",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "Cache entries expire",
                        "expected_status": "answered",
                        "expected_source_paths": ["note.md"],
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["import", str(source), "--workspace", str(workspace)]).exit_code == 0
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert (
        runner.invoke(
            app,
            ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"] == {
        "answer_accuracy": 100.0,
        "page_route_recall_at_3": 100.0,
        "source_recall_at_3": 100.0,
        "fact_source_recall": 100.0,
        "fact_selection_accuracy": 100.0,
        "citation_grounding_accuracy": 100.0,
        "multi_source_coverage": 0.0,
        "abstention_accuracy": 0.0,
        "selective_accuracy": 100.0,
        "coverage": 100.0,
        "risk": 0.0,
        "risk_coverage": [
            {
                "threshold": 75.0,
                "coverage": 100.0,
                "selective_accuracy": 100.0,
                "risk": 0.0,
            }
        ],
        "repository_path_isolation_accuracy": 0.0,
        "average_wiki_pages_read": 1.0,
        "average_raw_sources_read": 0.0,
        "average_evidence_characters": 41.0,
        "error_classification_counts": {"none": 1},
    }
    assert payload["raw_fts_baseline"]["expected_source_recall_at_3"] == 100.0
    assert payload["cases"][0]["memoryforge"]["routed_source_paths"] == ["note.md"]
    assert payload["cases"][0]["memoryforge"]["error_classification"] == "none"


def test_eval_does_not_count_an_answer_from_the_wrong_source_as_correct(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {
            "cache.md": "# Cache policy\n\nCache entries expire after sixty seconds.\n",
            "deploy.md": "# Deployment\n\nDeployment runs every Friday.\n",
        },
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "mixed",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "When do cache entries expire?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["sixty", "seconds"],
                    },
                    {
                        "id": "deploy-day-wrong-source",
                        "category": "single_hop",
                        "question": "When does deployment run every week?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["Friday"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["answer_accuracy"] == 50.0
    assert payload["memoryforge"]["citation_grounding_accuracy"] == 100.0
    assert payload["memoryforge"]["source_recall_at_3"] == 50.0


def test_eval_uses_daily_debug_query_and_separates_raw_audit_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {"cache.md": "# Cache policy\n\nCache entries expire after sixty seconds.\n"},
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "cache",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "When do cache entries expire?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_flags: list[bool] = []
    real_answer = evaluation_module.answer_question

    def spy_answer_question(workspace_root: Path, question: str, **kwargs: object):
        debug_flags.append(bool(kwargs.get("debug", False)))
        return real_answer(workspace_root, question, **kwargs)

    monkeypatch.setattr(evaluation_module, "answer_question", spy_answer_question)

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert debug_flags == [True]
    assert payload["memoryforge"]["average_raw_sources_read"] == 0.0
    assert payload["memoryforge"]["average_evidence_characters"] > 0.0


def test_eval_does_not_count_an_answer_without_citations_as_grounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {"cache.md": "# Cache policy\n\nCache entries expire after sixty seconds.\n"},
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "missing-citation",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "When do cache entries expire?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        evaluation_module,
        "answer_question",
        lambda *args, **kwargs: {
            "status": "answered",
            "answer": "Cache entries expire after sixty seconds.",
            "citations": [],
            "wiki_pages": [],
            "trace": [],
        },
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["citation_grounding_accuracy"] == 0.0


def test_eval_keeps_same_relative_paths_separate_across_git_repositories(
    tmp_path: Path,
) -> None:
    first_checkout = _create_git_checkout(tmp_path / "first", "blue")
    second_checkout = _create_git_checkout(tmp_path / "second", "red")
    workspace = init_workspace(tmp_path / "workspace")
    first_repository = register_git_checkout(
        workspace,
        first_checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    second_repository = register_git_checkout(
        workspace,
        second_checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    sync_git_checkout(workspace, first_repository.repository_id)
    sync_git_checkout(workspace, second_repository.repository_id)
    runner = CliRunner()
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output

    config = tmp_path / "multi-repository-suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "multi-repository",
                "cases": [
                    {
                        "id": "blue-scheduler",
                        "category": "single_hop",
                        "question": "scheduler",
                        "expected_status": "answered",
                        "expected_source_paths": ["README.md"],
                        "required_terms": ["blue"],
                        "forbidden_terms": ["red"],
                        "repository_id": first_repository.repository_id,
                    },
                    {
                        "id": "red-scheduler",
                        "category": "single_hop",
                        "question": "scheduler",
                        "expected_status": "answered",
                        "expected_source_paths": ["README.md"],
                        "required_terms": ["red"],
                        "forbidden_terms": ["blue"],
                        "repository_id": second_repository.repository_id,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["answer_accuracy"] == 100.0
    assert payload["memoryforge"]["source_recall_at_3"] == 100.0
    assert [case["memoryforge"]["cited_source_paths"] for case in payload["cases"]] == [
        ["README.md"],
        ["README.md"],
    ]
    assert payload["memoryforge"]["repository_path_isolation_accuracy"] == 100.0


def test_repository_aware_source_matching_rejects_same_path_from_wrong_repository() -> None:
    first_repository = "a" * 64
    second_repository = "b" * 64
    expected = {(first_repository, "README.md")}
    wrong = {(second_repository, "README.md")}

    assert not evaluation_module._sources_recalled(
        wrong,
        expected,
        require_all=True,
    )
    assert not evaluation_module._repository_paths_isolated(wrong, expected)


def test_repository_isolation_does_not_duplicate_source_recall() -> None:
    repository = "a" * 64
    expected = {(repository, "expected.md")}
    wrong_path_same_repository = {(repository, "other.md")}

    assert not evaluation_module._sources_recalled(
        wrong_path_same_repository,
        expected,
        require_all=True,
    )
    assert evaluation_module._repository_paths_isolated(wrong_path_same_repository, expected)


def test_code_wiki_fact_wrapper_is_grounded_by_its_canonical_code() -> None:
    quote = (
        "`mgr.ANASMgr` (struct): "
        "`ANASMgr struct { FrameWork *mgr.Mgr GreyManager *greyimpl.IlmfGreyManager }`"
    )
    excerpt = """
    type ANASMgr struct {
        FrameWork *mgr.Mgr
        GreyManager *greyimpl.IlmfGreyManager
    }
    """

    assert evaluation_module._citation_quote_grounded(quote, excerpt)


def test_cross_repository_labels_align_repositories_with_source_paths() -> None:
    first_repository = "a" * 64
    second_repository = "b" * 64
    case = evaluation_module.EvaluationCase(
        id="cross-repository",
        category="cross_repository",
        question="Compare both schedulers",
        expected_status="answered",
        expected_source_paths=("first/README.md", "second/README.md"),
        required_terms=("scheduler",),
        repository_ids=(first_repository, second_repository),
    )

    assert evaluation_module._expected_sources(
        case.expected_source_paths,
        case.repository_ids,
    ) == {
        (first_repository, "first/README.md"),
        (second_repository, "second/README.md"),
    }


def test_failure_classification_separates_route_fact_and_answer_failures() -> None:
    case = evaluation_module.EvaluationCase(
        id="cache-expiry",
        category="exact_symbol",
        question="What is the cache expiry?",
        expected_status="answered",
        expected_source_paths=("cache.md",),
        required_terms=("sixty",),
    )
    common = {
        "case": case,
        "answer_status": "answered",
        "answer_correct": False,
        "citation_grounded": True,
        "citations_current": True,
        "all_expected_sources_cited": True,
        "repository_path_isolated": True,
    }

    assert (
        evaluation_module._classify_error(
            **common,
            page_route_recalled=False,
            fact_selection_correct=False,
        )
        == "page_route_miss"
    )
    assert (
        evaluation_module._classify_error(
            **common,
            page_route_recalled=True,
            fact_selection_correct=False,
        )
        == "fact_selection_miss"
    )
    assert (
        evaluation_module._classify_error(
            **common,
            page_route_recalled=True,
            fact_selection_correct=True,
        )
        == "wrong_answer"
    )


@pytest.mark.parametrize(
    "category",
    ["exact_symbol", "code_behavior", "temporal_update"],
)
def test_extended_single_source_categories_are_valid(category: str) -> None:
    case = evaluation_module.EvaluationCase(
        id=f"{category}-case",
        category=category,
        question="What does the source say?",
        expected_status="answered",
        expected_source_paths=("source.md",),
        required_terms=("source",),
    )

    assert case.category == category


def test_eval_cites_all_expected_sources_for_multi_source_case(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {
            "cache.md": "# Cache\n\nCache expires after sixty seconds.\n",
            "deploy.md": "# Deploy\n\nDeployment runs every Friday.\n",
        },
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "multi-source",
                "cases": [
                    {
                        "id": "cache-and-deploy",
                        "category": "multi_source",
                        "question": "Cache expires sixty seconds and deployment Friday",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md", "deploy.md"],
                        "required_terms": ["sixty", "Friday"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["multi_source_coverage"] == 100.0
    assert payload["cases"][0]["memoryforge"]["cited_source_paths"] == [
        "cache.md",
        "deploy.md",
    ]


def test_eval_raw_baseline_requires_all_sources_for_multi_source_recall(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {
            "cache.md": "# Cache\n\nCache expires after sixty seconds.\n",
            "deploy.md": "# Deploy\n\nDeployment runs every Friday.\n",
        },
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "raw-multi-source",
                "cases": [
                    {
                        "id": "cache-and-deploy",
                        "category": "multi_source",
                        "question": "Cache expires sixty seconds",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md", "deploy.md"],
                        "required_terms": ["sixty"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["cases"][0]["raw_fts_baseline"]["expected_source_in_top_3"] is True
    assert payload["cases"][0]["raw_fts_baseline"]["expected_sources_in_top_3"] is False
    assert payload["raw_fts_baseline"]["expected_source_recall_at_3"] == 0.0
    assert payload["raw_fts_baseline"]["multi_source_coverage"] == 0.0


def test_eval_accepts_and_scores_unanswerable_case(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {"cache.md": "# Cache\n\nCache expires after sixty seconds.\n"},
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "abstention",
                "cases": [
                    {
                        "id": "unknown-database-sharding",
                        "category": "unanswerable",
                        "question": "How should database shards rebalance?",
                        "expected_status": "unknown",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["abstention_accuracy"] == 100.0
    assert payload["cases"][0]["memoryforge"]["citation_grounded"] is True


def _build_applied_workspace(
    tmp_path: Path,
    monkeypatch,
    sources: dict[str, str],
) -> tuple[CliRunner, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    for name, text in sources.items():
        source = repository / name
        source.write_text(text, encoding="utf-8")
        imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
        assert imported.exit_code == 0, imported.output
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    return runner, workspace


def _create_git_checkout(root: Path, color: str) -> Path:
    checkout = root / "checkout"
    checkout.mkdir(parents=True)
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text(
        f"# Shared scheduler\n\nThis repository owns the {color} scheduler.\n",
        encoding="utf-8",
    )
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-m", f"Add {color} scheduler")
    return checkout


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
