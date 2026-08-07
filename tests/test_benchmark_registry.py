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
        "suite_count": 12,
        "experiment_count": 5,
        "evidence_count": 62,
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


def test_benchmark_registry_cannot_self_drop_negative_evidence(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    experiment["required_evidence_statuses"] = ["accepted_development"]
    experiment["evidence"] = [
        artifact
        for artifact in experiment["evidence"]
        if artifact["status"] == "accepted_development"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_cannot_drop_multi_source_baseline(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "multi-source-coverage-selection"
    )
    experiment["evidence"] = [
        artifact for artifact in experiment["evidence"] if artifact["status"] != "rejected"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_multi_source_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "multi-source-coverage-selection"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_cannot_drop_folder_import_baseline(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "folder-import-lifecycle"
    )
    experiment["evidence"] = [
        artifact for artifact in experiment["evidence"] if artifact["status"] != "rejected"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_folder_import_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "folder-import-lifecycle"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_cannot_drop_github_thread_baseline(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "github-thread-import-lifecycle"
    )
    experiment["evidence"] = [
        artifact for artifact in experiment["evidence"] if artifact["status"] != "rejected"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_github_thread_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "github-thread-import-lifecycle"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_requires_local_gate_for_acceptance(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    accepted = next(
        artifact
        for artifact in experiment["evidence"]
        if artifact["status"] == "accepted_development"
    )
    del accepted["acceptance_evidence"]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_reassigned_acceptance_status(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    experiment["evidence"][4]["status"] = "accepted_development"
    experiment["evidence"][13]["status"] = "accepted_development_superseded"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="Evidence identity changed"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_substituted_regression_evidence(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    experiment["evidence"][0]["regression_evidence"] = experiment["evidence"][1][
        "regression_evidence"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="regression Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_local_gate_identity(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    experiment["evidence"][-1]["acceptance_evidence"]["sha256"] = "0" * 64
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_duplicate_support_case_identities() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item
        for item in registry["experiments"]
        if item["suite_id"] == "support-score.learn-claude-code"
    )
    evidence = json.loads(
        (validator.REPO_ROOT / "demo/results/support_score_development_candidate_9.json").read_text(
            encoding="utf-8"
        )
    )
    cases = evidence["development"]["evaluation"]["cases"]

    assert validator._support_case_identities_match(
        cases,
        experiment["splits"]["development"],
    )
    assert not validator._support_case_identities_match(
        [cases[0] for _ in cases],
        experiment["splits"]["development"],
    )


def test_benchmark_registry_recomputes_support_replay_hashes() -> None:
    evidence = json.loads(
        (
            validator.REPO_ROOT / "demo/results/support_score_development_candidate_11.json"
        ).read_text(encoding="utf-8")
    )
    runs = evidence["runs"]

    assert validator._support_runs_are_deterministic(runs)
    runs[1]["structural_sha256"] = "0" * 64
    assert not validator._support_runs_are_deterministic(runs)
