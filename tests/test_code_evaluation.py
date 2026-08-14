from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from memoryforge.code.code_models import CitedStatement, ModuleNarrative
from memoryforge.query.provider import ProviderUnavailableError

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_code_wiki_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_code_wiki_benchmark", _SCRIPT)
assert _spec and _spec.loader
run_code_wiki_benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_code_wiki_benchmark)

_NARRATIVE_SCRIPT = (
    Path(__file__).resolve().parent.parent / "demo" / "run_code_module_narrative_benchmark.py"
)
_narrative_spec = importlib.util.spec_from_file_location(
    "run_code_module_narrative_benchmark",
    _NARRATIVE_SCRIPT,
)
assert _narrative_spec and _narrative_spec.loader
run_code_module_narrative_benchmark = importlib.util.module_from_spec(_narrative_spec)
sys.modules[_narrative_spec.name] = run_code_module_narrative_benchmark
_narrative_spec.loader.exec_module(run_code_module_narrative_benchmark)


class _BenchmarkNarrativeProvider:
    _TEXT = {
        "py": (
            "py app helpers 通过 run_local 调用 local，"
            "并由 run_imported 使用 normalize strip value。"
        ),
        "go": "go Meter 由 NewMeter 创建，Record 调用 helper 与 Reset，Use 也调用 Reset。",
        "ts": "ts service 的 run 创建 Service，greet 调用 helper 对字符串执行 trim。",
    }

    def summarize_code_module(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> ModuleNarrative:
        payload = json.loads(messages[-1]["content"])
        path = payload["module"]["path"]
        source_indexes: dict[str, int] = {}
        for citation in payload["citations"]:
            source_indexes.setdefault(citation["source_path"], citation["index"])
        indexes = tuple(source_indexes.values())
        text = self._TEXT[path]
        return ModuleNarrative(
            purpose=CitedStatement(text=text, citation_indexes=indexes),
            responsibilities=(CitedStatement(text=text, citation_indexes=indexes),),
            key_flows=(CitedStatement(text=text, citation_indexes=indexes),),
        )


class _FallbackBenchmarkNarrativeProvider(_BenchmarkNarrativeProvider):
    def summarize_code_module(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> ModuleNarrative:
        payload = json.loads(messages[-1]["content"])
        if payload["module"]["path"] == "go":
            raise ProviderUnavailableError("temporary failure")
        return super().summarize_code_module(messages)


def test_code_module_narrative_development_suite_executes(
    tmp_path: Path,
) -> None:
    evidence = run_code_module_narrative_benchmark.build_evidence(
        tmp_path / "narrative-benchmark",
        provider=_BenchmarkNarrativeProvider(),
    )

    evaluation = evidence["evaluation"]
    assert evaluation["case_count"] == 20
    assert evaluation["metrics"] == {
        "fact_coverage": 1.0,
        "citation_grounding": 1.0,
        "synthesis_success_rate": 1.0,
    }
    assert evaluation["gates"] == {
        "fact_coverage": True,
        "citation_grounding": True,
    }
    assert all(case["covered"] for case in evaluation["cases"])
    assert evidence["workflow"]["projection_rebuild"] == "passed"
    assert evidence["workflow"]["lint"]["status"] == "clean"


def test_code_module_narrative_benchmark_records_provider_fallback(
    tmp_path: Path,
) -> None:
    evidence = run_code_module_narrative_benchmark.build_evidence(
        tmp_path / "narrative-fallback-benchmark",
        provider=_FallbackBenchmarkNarrativeProvider(),
    )

    evaluation = evidence["evaluation"]
    assert evaluation["metrics"] == {
        "fact_coverage": 0.7,
        "citation_grounding": 1.0,
        "synthesis_success_rate": 0.667,
    }
    assert evaluation["gates"] == {
        "fact_coverage": False,
        "citation_grounding": True,
    }
    assert {
        case["synthesis_status"] for case in evaluation["cases"] if case["module_path"] == "go"
    } == {"fallback"}


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
