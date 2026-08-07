from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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
        "experiment_count": 7,
        "evidence_count": 77,
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


def test_benchmark_registry_cannot_drop_static_showcase_negatives(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "static-showcase"
    )
    experiment["evidence"] = [
        artifact for artifact in experiment["evidence"] if artifact["status"] != "rejected"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_static_showcase_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "static-showcase"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_cannot_drop_cross_platform_baseline(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    experiment["evidence"] = [
        artifact for artifact in experiment["evidence"] if artifact["status"] != "rejected"
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_cross_platform_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_contradictory_cross_platform_case_evidence() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    artifact = next(
        item
        for item in experiment["evidence"]
        if item["status"] == "accepted_development_superseded"
    )
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["evaluation"]["cases"][0]["status"] = "failed"
    evaluation_sha256 = hashlib.sha256(
        json.dumps(
            payload["development"]["evaluation"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    for run in payload["runs"]:
        run["evaluation_sha256"] = evaluation_sha256

    with pytest.raises(ValueError, match="cross-platform delivery case Evidence changed"):
        validator._validate_pytest_component_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_rejected_cross_platform_cases() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    artifact = next(item for item in experiment["evidence"] if item["status"] == "rejected")
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["evaluation"]["cases"][0]["status"] = "passed"
    payload["development"]["evaluation"]["metrics"] = experiment["expected_metrics"]["development"]
    evaluation_sha256 = hashlib.sha256(
        json.dumps(
            payload["development"]["evaluation"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    for run in payload["runs"]:
        run["evaluation_sha256"] = evaluation_sha256

    with pytest.raises(ValueError, match="cross-platform delivery case Evidence changed"):
        validator._validate_pytest_component_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_loose_cross_platform_json_types() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    artifact = next(item for item in experiment["evidence"] if item["evidence_revision"] == 3)
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["schema_version"] = True
    payload["suite_revision"] = True
    payload["development"]["case_count"] = 7.0
    payload["development"]["evaluation"]["case_count"] = 7.0

    with pytest.raises(ValueError, match="pytest component experiment Evidence contract failed"):
        validator._validate_pytest_component_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_artifact_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    relative = os.path.relpath(outside, validator.REPO_ROOT)

    with pytest.raises(ValueError, match="artifact path is unsafe"):
        validator._validate_artifact(
            {
                "path": relative,
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            },
            "outside",
        )


def test_benchmark_registry_cannot_drop_cross_platform_linux_evidence(
    tmp_path: Path,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 4)
    del candidate["linux_evidence"]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="Linux Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_binds_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 4)
    linux = candidate["linux_evidence"]
    payload = json.loads((validator.REPO_ROOT / linux["path"]).read_text(encoding="utf-8"))
    payload["runtime"]["hosted_runner"] = True

    monkeypatch.setattr(validator.json, "loads", lambda _value: payload)
    with pytest.raises(ValueError, match="Linux Evidence contract failed"):
        validator._validate_linux_evidence(
            experiment,
            candidate,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_contradictory_showcase_case_evidence() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "static-showcase"
    )
    artifact = next(
        item for item in experiment["evidence"] if item["status"] == "accepted_development"
    )
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["evaluation"]["cases"][0]["status"] = "failed"
    payload["development"]["evaluation"]["cases"][0]["error_classification"] = "ASSERTION_FAILURE"
    evaluation_sha256 = hashlib.sha256(
        json.dumps(
            payload["development"]["evaluation"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    for run in payload["runs"]:
        run["evaluation_sha256"] = evaluation_sha256

    with pytest.raises(ValueError, match="accepted static-Showcase Evidence contract failed"):
        validator._validate_static_showcase_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_rejected_showcase_split_identity() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "static-showcase"
    )
    artifact = next(item for item in experiment["evidence"] if item["evidence_revision"] == 1)
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["sha256"] = "0" * 64
    payload["confirmation"]["sha256"] = "1" * 64

    with pytest.raises(ValueError, match="rejected static-Showcase Evidence"):
        validator._validate_static_showcase_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_loose_showcase_json_types() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "static-showcase"
    )
    rejected = next(item for item in experiment["evidence"] if item["evidence_revision"] == 1)
    rejected_payload = json.loads(
        (validator.REPO_ROOT / rejected["path"]).read_text(encoding="utf-8")
    )
    rejected_payload["suite_revision"] = True
    rejected_payload["development"]["pytest"]["passed"] = False
    rejected_payload["development"]["failures"].append("ignored")
    with pytest.raises(ValueError, match="rejected static-Showcase Evidence"):
        validator._validate_static_showcase_experiment_payload(
            experiment,
            rejected,
            rejected_payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )

    accepted = next(
        item for item in experiment["evidence"] if item["status"] == "accepted_development"
    )
    accepted_payload = json.loads(
        (validator.REPO_ROOT / accepted["path"]).read_text(encoding="utf-8")
    )
    accepted_payload["development"]["evaluation"]["metrics"]["pass_rate"] = 100
    accepted_payload["development"]["evaluation"]["metrics"]["failed_cases"] = False
    with pytest.raises(ValueError, match="accepted static-Showcase Evidence"):
        validator._validate_static_showcase_experiment_payload(
            experiment,
            accepted,
            accepted_payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


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


def test_benchmark_registry_rejects_stale_final_acceptance_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_path = (
        validator.REPO_ROOT / "demo/results/github_thread_import_candidate_3_local_gate.json"
    )
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["local_gate"]["registry_validation"]["experiment_count"] = 4
    monkeypatch.setattr(
        validator,
        "_validate_artifact",
        lambda artifact, suite_id: None,
    )
    monkeypatch.setattr(
        validator.Path,
        "read_text",
        lambda path, encoding="utf-8": json.dumps(payload),
    )
    experiment = {
        "suite_id": "github-thread-import-lifecycle",
        "suite_revision": 1,
    }
    development_artifact = {
        "path": "demo/results/github_thread_import_development_candidate_3.json",
        "sha256": "3c32675802191dbeec6c8477e0b1abcb618b115120575abc0e6509f8dc565b2c",
        "memoryforge_commit": "c6f329152dac002ecead2f8d8bebcb002865aff6",
        "status": "accepted_development",
        "acceptance_evidence": {
            "path": "demo/results/github_thread_import_candidate_3_local_gate.json",
            "memoryforge_commit": payload["memoryforge_commit"],
            "passed": True,
        },
    }
    confirmation = {
        "path": "demo/evaluation/github_thread_import_confirmation.json",
        "sha256": "45de8d774b8d02b0112571ceb8c3b4d43db589cb0a68dbb96579aa2327ad24b0",
    }

    with pytest.raises(ValueError, match="acceptance Evidence contract failed"):
        validator._validate_acceptance_evidence(
            experiment,
            development_artifact,
            confirmation,
        )


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
