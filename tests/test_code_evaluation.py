from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_code_wiki_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_code_wiki_benchmark", _SCRIPT)
assert _spec and _spec.loader
run_code_wiki_benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_code_wiki_benchmark)


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
