from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "demo" / "run_agent_memory_v2_benchmark.py"
VALIDATOR_PATH = REPO_ROOT / "demo" / "validate_agent_memory_v2_results.py"
REGISTRY_PATH = REPO_ROOT / "demo" / "evaluation" / "agent_memory_v2_registry.json"
DEV_PATH = REPO_ROOT / "demo" / "evaluation" / "agent_memory_v2_development.json"
CONFIRM_PATH = REPO_ROOT / "demo" / "evaluation" / "agent_memory_v2_confirmation.json"
HOLDOUT_PATH = REPO_ROOT / "demo" / "evaluation" / "agent_memory_v2_holdout.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load_module("run_agent_memory_v2_benchmark", RUNNER_PATH)
validator = _load_module("validate_agent_memory_v2_results", VALIDATOR_PATH)


def test_registry_json_schema_fields() -> None:
    registry = cast(dict[str, Any], json.loads(REGISTRY_PATH.read_text(encoding="utf-8")))
    assert registry["schema_version"] == 1
    assert registry["suite_id"] == "agent-memory-v2-hybrid-retrieval"
    assert registry["suite_revision"] >= 1
    assert "datasets" in registry
    splits = registry["splits"]
    assert "development" in splits
    assert "confirmation" in splits
    assert "holdout" in splits
    assert splits["development"]["case_count"] == 10
    assert splits["confirmation"]["case_count"] == 5
    assert splits["holdout"]["case_count"] == 5


def test_development_ten_cases_valid_schema() -> None:
    dev = cast(dict[str, Any], json.loads(DEV_PATH.read_text(encoding="utf-8")))
    cases = cast(list[dict[str, Any]], dev["cases"])
    assert len(cases) == 10
    ids = set()
    for case in cases:
        for field in ("id", "question", "expected_status", "expected_sources"):
            assert field in case, f"case {case.get('id')} missing {field}"
        assert isinstance(case["id"], str) and len(case["id"]) > 0
        assert isinstance(case["question"], str) and len(case["question"]) > 0
        assert case["expected_status"] in {"answered", "unanswerable"}
        assert isinstance(case["expected_sources"], list)
        ids.add(case["id"])
    assert len(ids) == 10


def test_confirmation_five_cases_valid_schema() -> None:
    confirm = cast(dict[str, Any], json.loads(CONFIRM_PATH.read_text(encoding="utf-8")))
    cases = cast(list[dict[str, Any]], confirm["cases"])
    assert len(cases) == 5


def test_holdout_placeholders_schema() -> None:
    holdout = cast(dict[str, Any], json.loads(HOLDOUT_PATH.read_text(encoding="utf-8")))
    assert holdout["status"] == "frozen_pending"
    assert holdout["case_count"] == 5
    assert len(holdout["cases"]) == 5
    for c in holdout["cases"]:
        assert c["expected_sources"] == []


def test_runner_produces_valid_schema(tmp_path: Path) -> None:
    result = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )

    assert isinstance(result, dict)
    assert result["schema_version"] == 1
    assert result["suite_id"] == "agent-memory-v2-hybrid-retrieval"

    splits = cast(dict[str, Any], result["splits"])
    dev = cast(dict[str, Any], splits["development"])
    dev_summary = cast(dict[str, Any], dev["summary"])
    dev_cases = cast(list[dict[str, Any]], dev["cases"])

    assert dev_summary["case_count"] == 10
    assert len(dev_cases) == 10

    for c in dev_cases:
        for field in (
            "id",
            "retrieved_pages",
            "routes_used",
            "semantic_status",
            "page_recall_at_3",
            "source_recall_at_3",
            "reciprocal_rank",
            "privacy_leak_detected",
        ):
            assert field in c, f"case {c.get('id')} missing field {field}"

    assert "deterministic_hash" in result
    assert isinstance(result["deterministic_hash"], str)
    assert len(result["deterministic_hash"]) == 64


def test_runner_output_validates_with_default_thresholds(tmp_path: Path) -> None:
    result = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )

    out = tmp_path / "result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    summary = validator.validate_results_file(
        out,
        source_recall_threshold=0.0,
        page_recall_threshold=0.0,
        max_privacy_leaks=99,
    )
    assert summary["status"] == "valid"
    assert summary["gates"]["deterministic_hash_match"] is True


def test_validator_detects_hash_mismatch(tmp_path: Path) -> None:
    result = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )
    result["deterministic_hash"] = "0" * 64
    summary = validator.validate_results(
        result,
        source_recall_threshold=0.0,
        page_recall_threshold=0.0,
        max_privacy_leaks=99,
    )
    assert summary["gates"]["deterministic_hash_match"] is False
    assert summary["passed"] is False


def test_validator_detects_recall_below_threshold(tmp_path: Path) -> None:
    result = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )
    summary = validator.validate_results(
        result,
        source_recall_threshold=1.5,
        page_recall_threshold=1.5,
        max_privacy_leaks=99,
        require_deterministic=False,
    )
    assert summary["gates"]["source_recall_threshold"] is False
    assert summary["passed"] is False


def test_runner_twice_produces_same_deterministic_hash() -> None:
    r1 = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )
    r2 = runner.run_benchmark(
        registry_path=REGISTRY_PATH,
        dev_path=DEV_PATH,
        confirm_path=CONFIRM_PATH,
    )
    assert r1["deterministic_hash"] == r2["deterministic_hash"]

    r1_dev_cases = cast(list[dict[str, Any]], r1["splits"]["development"]["cases"])
    r2_dev_cases = cast(list[dict[str, Any]], r2["splits"]["development"]["cases"])
    for a, b in zip(r1_dev_cases, r2_dev_cases, strict=True):
        assert a["id"] == b["id"]
        assert a["source_recall_at_3"] == b["source_recall_at_3"]
        assert a["retrieved_pages"] == b["retrieved_pages"]
