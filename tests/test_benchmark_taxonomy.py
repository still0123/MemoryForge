from __future__ import annotations

import json
from pathlib import Path


def test_benchmark_taxonomy_retains_every_failure_classification() -> None:
    root = Path(__file__).resolve().parent.parent
    evidence = json.loads(
        (root / "demo/results/benchmark_taxonomy_baseline.json").read_text(encoding="utf-8")
    )

    assert evidence["evaluation_worktree_dirty"] is False
    assert evidence["evaluated_case_count"] == 116
    assert evidence["failed_case_count"] == len(evidence["failures"]) == 45
    assert sum(item["case_count"] for item in evidence["per_suite"]) == 116
    assert evidence["error_classification_counts"] == {
        "fact_selection_miss": 29,
        "multi_source_incomplete": 1,
        "none": 71,
        "page_route_miss": 11,
        "wrong_abstention": 4,
    }
    assert all(failure["error_classification"] != "none" for failure in evidence["failures"])
    assert len(
        {(failure["suite_id"], failure["split"], failure["id"]) for failure in evidence["failures"]}
    ) == len(evidence["failures"])
    assert evidence["macro_metric_suite_counts"]["repository_path_isolation_accuracy"] == 6
    assert evidence["unrun_frozen_split"]["status"] == "not_run"
