from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "validate_benchmark_registry.py"
_SPEC = importlib.util.spec_from_file_location("validate_benchmark_registry", _SCRIPT)
assert _SPEC and _SPEC.loader
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)


def test_benchmark_registry_binds_all_release_artifacts() -> None:
    summary = validator.validate_registry()

    assert summary == {
        "status": "valid",
        "suite_count": 11,
        "evidence_count": 15,
        "qa_case_count": 121,
        "qa_case_types_present": [
            "code_behavior",
            "cross_repository",
            "exact_symbol",
            "multi_source",
            "paraphrase",
            "single_hop",
            "temporal_update",
            "unanswerable",
        ],
        "suite_types": [
            "code_wiki_qa",
            "code_wiki_structure",
            "document_wiki_qa",
            "source_lifecycle",
        ],
    }


def test_benchmark_registry_rejects_duplicate_suite_ids(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    registry["suites"][1]["suite_id"] = registry["suites"][0]["suite_id"]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="suite IDs must be unique"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_artifact_hash_drift(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    registry["suites"][0]["splits"]["development"]["sha256"] = "0" * 64
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        validator.validate_registry(path)
