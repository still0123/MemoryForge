from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_code_wiki_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_code_wiki_benchmark", _SCRIPT)
assert _spec and _spec.loader
run_code_wiki_benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_code_wiki_benchmark)


def test_code_module_narrative_development_suite_is_frozen() -> None:
    root = Path(__file__).resolve().parent.parent
    suite = json.loads(
        (root / "demo/evaluation/code_module_narrative_development.json").read_text(
            encoding="utf-8"
        )
    )
    cases = suite["cases"]

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert [case["category"] for case in cases].count("module_responsibility") == 10
    assert [case["category"] for case in cases].count("call_flow") == 10
    assert suite["acceptance"] == {
        "citation_grounding": 1.0,
        "minimum_fact_coverage": 0.8,
    }
    fixture = root / suite["fixture"]
    assert all(
        (fixture / source_path).is_file()
        for case in cases
        for source_path in case["expected_source_paths"]
    )


def test_code_wiki_benchmark_closes_known_gaps_without_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = run_code_wiki_benchmark.build_evidence(tmp_path / "benchmark")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Host Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "host@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Host Committer")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "host@example.invalid")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing.gitconfig"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "missing-worktree"))
    assert evidence == run_code_wiki_benchmark.build_evidence(tmp_path / "benchmark-replay")

    metrics = evidence["evaluation"]["metrics"]
    assert metrics["expected_source_coverage"] == 100.0
    assert metrics["symbol_recall"] == 100.0
    assert metrics["core_relation_recall"] == 100.0
    assert metrics["known_gap_relation_recall"] == 100.0
    assert metrics["overall_relation_recall"] == 100.0
    assert metrics["module_assignment_accuracy"] == 100.0
    assert metrics["citation_grounding_accuracy"] == 100.0
    assert metrics["mermaid_edge_coverage"] == 100.0
    assert metrics["architecture_citation_coverage"] == 100.0
    assert metrics["architecture_mermaid_determinism"] == 100.0
    assert metrics["deterministic_replay"] == 100.0
    assert evidence["workflow"]["lint"]["status"] == "clean"
    assert evidence["incremental"]["passed"] is True
    assert evidence["incremental"]["changed_page_ratio"] == 0.2
