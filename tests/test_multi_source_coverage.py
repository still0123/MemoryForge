from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "demo/run_multi_source_coverage_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_multi_source_coverage_benchmark", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def _suite(split: str) -> dict[str, Any]:
    path = _ROOT / f"demo/evaluation/multi_source_coverage_{split}.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_frozen_suites_have_exact_schemas() -> None:
    benchmark._validate_suite(_suite("development"), expected_split="development")
    benchmark._validate_suite(_suite("confirmation"), expected_split="confirmation")


def test_suite_rejects_duplicate_case_ids() -> None:
    suite = copy.deepcopy(_suite("development"))
    suite["cases"][1]["id"] = suite["cases"][0]["id"]

    with pytest.raises(ValueError, match="case IDs must be unique"):
        benchmark._validate_suite(suite, expected_split="development")


def test_suite_rejects_source_quota_above_citation_budget() -> None:
    suite = copy.deepcopy(_suite("development"))
    suite["cases"][0]["required_source_count"] = suite["cases"][0]["max_citations"] + 1

    with pytest.raises(ValueError, match="case budget"):
        benchmark._validate_suite(suite, expected_split="development")
