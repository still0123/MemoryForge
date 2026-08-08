from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import tarfile
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
        "experiment_count": 8,
        "evidence_count": 112,
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


def test_benchmark_registry_cannot_drop_release_candidate_baseline(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    experiment["evidence"] = []
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="requires generated evidence"):
        validator.validate_registry(path)


def test_benchmark_registry_pins_release_candidate_repository_commit(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    experiment["repositories"][0]["commit"] = "0" * 40
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata changed"):
        validator.validate_registry(path)


def test_benchmark_registry_keeps_release_candidate_holdout_closed(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    experiment["splits"]["holdout"]["status"] = "passed"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="split contract failed"):
        validator.validate_registry(path)


def test_benchmark_registry_binds_rejected_release_candidate_cases() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][0]
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["evaluation"]["cases"][0]["status"] = "passed"
    evaluation_sha256 = hashlib.sha256(
        json.dumps(
            payload["development"]["evaluation"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    for run in payload["runs"]:
        run["evaluation_sha256"] = evaluation_sha256

    with pytest.raises(ValueError, match="rejected release-candidate Evidence changed"):
        validator._validate_release_candidate_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_rejected_release_candidate_gates() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][0]
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["gates"]["failed_cases"] = True

    with pytest.raises(ValueError, match="rejected release-candidate Evidence changed"):
        validator._validate_release_candidate_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_release_candidate_boolean_identities() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][0]
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["schema_version"] = True
    payload["suite_revision"] = True

    with pytest.raises(ValueError, match="release-candidate experiment Evidence contract failed"):
        validator._validate_release_candidate_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_retains_release_sdist_regression() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][2]
    regression = artifact["regression_evidence"]
    payload = json.loads((validator.REPO_ROOT / regression["path"]).read_text(encoding="utf-8"))
    payload["release_build"]["output_created"] = True

    with pytest.raises(ValueError, match="sdist regression Evidence changed"):
        validator._validate_release_sdist_regression(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
            regression["memoryforge_commit"],
        )


def test_benchmark_registry_retains_release_static_review_regression() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][3]
    regression = artifact["regression_evidence"]
    payload = json.loads((validator.REPO_ROOT / regression["path"]).read_text(encoding="utf-8"))
    payload["review"]["p1"] = 4

    with pytest.raises(ValueError, match="static review Evidence changed"):
        validator._validate_release_sdist_regression(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
            regression["memoryforge_commit"],
        )


def test_benchmark_registry_requires_release_candidate_local_gates(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    experiment["evidence"][1].pop("acceptance_evidence")
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance Evidence history is incomplete"):
        validator.validate_registry(path)


def test_benchmark_registry_binds_release_candidate_platform_results() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][1]
    payload = json.loads(
        (validator.REPO_ROOT / artifact["acceptance_evidence"]["path"]).read_text(encoding="utf-8")
    )
    payload["platforms"]["linux"]["local_gate"]["pytest"]["passed"] = 570

    with pytest.raises(ValueError, match="linux local gate Evidence changed"):
        validator._validate_release_candidate_acceptance_evidence(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_rejects_boolean_release_acceptance_identities() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][2]
    payload = json.loads(
        (validator.REPO_ROOT / artifact["acceptance_evidence"]["path"]).read_text(encoding="utf-8")
    )
    payload["schema_version"] = True
    payload["suite_revision"] = True

    with pytest.raises(ValueError, match="local gate Evidence contract failed"):
        validator._validate_release_candidate_acceptance_evidence(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_release_acceptance_commit() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = next(item for item in experiment["evidence"] if item["evidence_revision"] == 6)
    payload = json.loads(
        (validator.REPO_ROOT / artifact["acceptance_evidence"]["path"]).read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="local gate Evidence contract failed"):
        validator._validate_release_candidate_acceptance_evidence(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
            acceptance_commit="0" * 40,
        )
    payload["development_evidence"]["passed"] = 1
    with pytest.raises(ValueError, match="local gate Evidence contract failed"):
        validator._validate_release_candidate_acceptance_evidence(
            experiment,
            artifact,
            payload,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_closes_release_development_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = experiment["evidence"][-1]
    evidence = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))[
        "release_artifacts"
    ]
    source = validator.REPO_ROOT / evidence["artifact_root"]
    destination = tmp_path / evidence["artifact_root"]
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)

    assert validator._validate_release_development_artifacts(
        evidence,
        artifact["memoryforge_commit"],
        evidence_revision=artifact["evidence_revision"],
    )
    alternate = destination.parent / "copied-artifacts"
    destination.rename(alternate)
    destination.symlink_to(alternate, target_is_directory=True)
    assert not validator._validate_release_development_artifacts(
        evidence,
        artifact["memoryforge_commit"],
        evidence_revision=artifact["evidence_revision"],
    )
    destination.unlink()
    alternate.rename(destination)
    (destination / "benchmark-summary.json").unlink()
    assert not validator._validate_release_development_artifacts(
        evidence,
        artifact["memoryforge_commit"],
        evidence_revision=artifact["evidence_revision"],
    )


def test_benchmark_registry_rejects_loose_release_case_counts() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "release-candidate-delivery"
    )
    artifact = next(item for item in experiment["evidence"] if item["evidence_revision"] == 7)
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    payload["development"]["case_count"] = 6.0
    payload["development"]["evaluation"]["case_count"] = 6.0
    evaluation_sha256 = hashlib.sha256(
        json.dumps(
            payload["development"]["evaluation"],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    for run in payload["runs"]:
        run["evaluation_sha256"] = evaluation_sha256

    with pytest.raises(ValueError, match="release-candidate experiment Evidence contract failed"):
        validator._validate_release_candidate_experiment_payload(
            experiment,
            artifact,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_requires_semantic_release_support() -> None:
    commit = "1" * 40
    assert not validator._release_summary_contract(
        {"schema_version": 1, "memoryforge_commit": commit},
        commit,
    )
    assert not validator._release_drill_contract(
        {"schema_version": True, "memoryforge_commit": commit, "passed": True},
        commit,
    )


def test_benchmark_registry_consumes_local_gate_benchmark_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(
        (
            validator.REPO_ROOT / "demo/results/release_candidate_candidate_6_local_gates.json"
        ).read_text(encoding="utf-8")
    )
    local_gate = payload["platforms"]["macos"]["local_gate"]
    provenance_path = validator.REPO_ROOT / local_gate["artifact_files"]["provenance"]["path"]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["code_wiki"] = {}
    monkeypatch.setattr(validator.json, "loads", lambda _value: provenance)

    assert not validator._validate_bound_gate_artifacts(
        local_gate["artifact_files"],
        local_gate["artifacts"],
        payload["memoryforge_commit"],
        require_clean_sdist=True,
    )


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


def test_benchmark_registry_rejects_cross_platform_case_bool_int_aliases() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    artifact = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
    payload = json.loads((validator.REPO_ROOT / artifact["path"]).read_text(encoding="utf-8"))
    case = payload["development"]["evaluation"]["cases"][0]
    case["return_code"] = False
    case["timed_out"] = 0
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


def test_benchmark_registry_rejects_experiment_split_relabel(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    experiment["evidence"][-1]["split"] = "confirmation"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment Evidence split changed"):
        validator.validate_registry(path)


def test_benchmark_registry_rejects_float_split_counts(tmp_path: Path) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    experiment["splits"]["development"]["case_count"] = 7.0
    experiment["splits"]["confirmation"]["case_count"] = 3.0
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="experiment split contract failed"):
        validator.validate_registry(path)


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
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
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
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
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


def test_benchmark_registry_binds_final_development_runtime() -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
    payload = json.loads((validator.REPO_ROOT / candidate["path"]).read_text(encoding="utf-8"))
    payload["runtime"]["system"] = "Windows"

    with pytest.raises(ValueError, match="pytest component experiment Evidence contract failed"):
        validator._validate_pytest_component_experiment_payload(
            experiment,
            candidate,
            payload,
            experiment["splits"]["development"],
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_final_macos_gate_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
    acceptance = candidate["acceptance_evidence"]
    payload = json.loads((validator.REPO_ROOT / acceptance["path"]).read_text(encoding="utf-8"))
    payload["runtime"]["hosted_runner"] = True

    monkeypatch.setattr(validator.json, "loads", lambda _value: payload)
    with pytest.raises(ValueError, match="acceptance Evidence contract failed"):
        validator._validate_acceptance_evidence(
            experiment,
            candidate,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_binds_final_macos_gate_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
    acceptance = candidate["acceptance_evidence"]
    payload = json.loads((validator.REPO_ROOT / acceptance["path"]).read_text(encoding="utf-8"))
    payload["local_gate"]["pytest"] = {
        "passed": 1,
        "failed": 0,
        "coverage_percent": 0,
    }

    monkeypatch.setattr(validator.json, "loads", lambda _value: payload)
    with pytest.raises(ValueError, match="acceptance Evidence contract failed"):
        validator._validate_acceptance_evidence(
            experiment,
            candidate,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_requires_cross_platform_gate_ancestry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 10)
    monkeypatch.setattr(validator, "_git_commit_descends_from", lambda _commit, _ancestor: False)

    with pytest.raises(ValueError, match="acceptance Evidence contract failed"):
        validator._validate_acceptance_evidence(
            experiment,
            candidate,
            experiment["splits"]["confirmation"],
        )


def test_benchmark_registry_hashes_bound_gate_artifact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "1" * 40
    wheel = tmp_path / "memoryforge-0.2.1-py3-none-any.whl"
    sdist = tmp_path / "memoryforge-0.2.1.tar.gz"
    provenance = tmp_path / "release-provenance.json"
    sums = tmp_path / "SHA256SUMS"
    wheel.write_bytes(b"wheel")
    with tarfile.open(sdist, "w:gz") as archive:
        payload = b"[project]\nname = 'memoryforge'\n"
        member = tarfile.TarInfo("memoryforge-0.2.1/pyproject.toml")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    sdist_sha256 = hashlib.sha256(sdist.read_bytes()).hexdigest()
    provenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "memoryforge_commit": commit,
                "memoryforge_worktree_dirty": False,
                "package": {
                    "version": "0.2.1",
                    "wheel": wheel.name,
                    "wheel_sha256": wheel_sha256,
                    "import_path": ".venv/site-packages/memoryforge/__init__.py",
                    "import_from_fresh_venv": True,
                    "dependencies": {},
                },
                "runtime": {
                    "implementation": "CPython",
                    "python": "3.11.15",
                    "platform": "test-platform",
                },
                "checks": {
                    "pip_check": "passed",
                    "cli_help": "passed",
                    "code_wiki_benchmark": "passed",
                    "public_demo": "not_run",
                },
                "commands": [
                    "python -m pip install <wheel>",
                    "python -m pip check",
                    "python -m memoryforge --help",
                    "python demo/run_code_wiki_benchmark.py",
                ],
                "code_wiki": {
                    "evidence_sha256": "1" * 64,
                    "metrics": {"deterministic_replay": 100.0},
                    "gates": {"deterministic": True},
                    "incremental": {
                        "changed_symbols": [],
                        "expected_changed_symbols": [],
                        "changed_pages": [],
                        "expected_changed_pages": [],
                        "changed_page_ratio": 0.0,
                        "max_changed_page_ratio": 0.0,
                        "stable_symbol_ids": True,
                        "passed": True,
                    },
                },
                "public_demo": {
                    "status": "not_run",
                    "required_commit": "93f5dc05229da250b041850ad8deeeec886ef304",
                },
            }
        ),
        encoding="utf-8",
    )
    provenance_sha256 = hashlib.sha256(provenance.read_bytes()).hexdigest()
    sums.write_text(
        f"{wheel_sha256}  dist/{wheel.name}\n"
        f"{sdist_sha256}  dist/{sdist.name}\n"
        f"{provenance_sha256}  release-provenance.json\n",
        encoding="ascii",
    )
    digests = {
        "wheel_sha256": wheel_sha256,
        "sdist_sha256": sdist_sha256,
        "provenance_sha256": provenance_sha256,
        "sha256sums_sha256": hashlib.sha256(sums.read_bytes()).hexdigest(),
    }
    files = {
        name: {"path": path.name, "sha256": digests[f"{name}_sha256"]}
        for name, path in {
            "wheel": wheel,
            "sdist": sdist,
            "provenance": provenance,
            "sha256sums": sums,
        }.items()
    }
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)

    assert validator._validate_bound_gate_artifacts(
        files,
        digests,
        commit,
        require_clean_sdist=True,
    )
    provenance_payload = json.loads(provenance.read_text(encoding="utf-8"))
    provenance_payload["checks"]["pip_check"] = "failed"
    provenance_payload["note"] = "copied from /Users/private/workspace"
    provenance.write_text(json.dumps(provenance_payload), encoding="utf-8")
    digests["provenance_sha256"] = hashlib.sha256(provenance.read_bytes()).hexdigest()
    files["provenance"]["sha256"] = digests["provenance_sha256"]
    sums.write_text(
        f"{wheel_sha256}  dist/{wheel.name}\n"
        f"{sdist_sha256}  dist/{sdist.name}\n"
        f"{digests['provenance_sha256']}  release-provenance.json\n",
        encoding="ascii",
    )
    digests["sha256sums_sha256"] = hashlib.sha256(sums.read_bytes()).hexdigest()
    files["sha256sums"]["sha256"] = digests["sha256sums_sha256"]
    assert not validator._validate_bound_gate_artifacts(
        files,
        digests,
        commit,
        require_clean_sdist=True,
    )
    provenance_payload["checks"]["pip_check"] = "passed"
    provenance_payload.pop("note")
    provenance.write_text(json.dumps(provenance_payload), encoding="utf-8")
    provenance_sha256 = hashlib.sha256(provenance.read_bytes()).hexdigest()
    digests["provenance_sha256"] = provenance_sha256
    files["provenance"]["sha256"] = provenance_sha256
    with tarfile.open(sdist, "w:gz") as archive:
        payload = b"nested wheel"
        member = tarfile.TarInfo(
            "memoryforge-0.2.1/demo/results/artifacts/candidate/memoryforge.whl"
        )
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    digests["sdist_sha256"] = hashlib.sha256(sdist.read_bytes()).hexdigest()
    files["sdist"]["sha256"] = digests["sdist_sha256"]
    sums.write_text(
        f"{wheel_sha256}  dist/{wheel.name}\n"
        f"{digests['sdist_sha256']}  dist/{sdist.name}\n"
        f"{provenance_sha256}  release-provenance.json\n",
        encoding="ascii",
    )
    digests["sha256sums_sha256"] = hashlib.sha256(sums.read_bytes()).hexdigest()
    files["sha256sums"]["sha256"] = digests["sha256sums_sha256"]
    assert not validator._validate_bound_gate_artifacts(
        files,
        digests,
        commit,
        require_clean_sdist=True,
    )

    wheel.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        validator._validate_bound_gate_artifacts(
            files,
            digests,
            commit,
            require_clean_sdist=True,
        )


def test_benchmark_registry_rejects_private_or_failed_provenance() -> None:
    assert (
        validator._payload_private_detail_leaks(
            {"note": "artifact copied from /Users/private/workspace"}
        )
        == 1
    )
    assert (
        validator._payload_private_detail_leaks(
            {"note": "artifact", "checks": {"pip_check": "failed"}}
        )
        == 0
    )
    assert (
        validator._payload_private_detail_leaks(
            {"credentials": {"api_key": "sk-live-private-value"}}
        )
        == 2
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


def test_benchmark_registry_rejects_boolean_acceptance_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads(validator.DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    experiment = next(
        item for item in registry["experiments"] if item["suite_id"] == "cross-platform-delivery"
    )
    candidate = next(item for item in experiment["evidence"] if item["evidence_revision"] == 3)
    acceptance = candidate["acceptance_evidence"]
    payload = json.loads((validator.REPO_ROOT / acceptance["path"]).read_text(encoding="utf-8"))
    payload["schema_version"] = True
    payload["suite_revision"] = True

    monkeypatch.setattr(validator.json, "loads", lambda _value: payload)
    with pytest.raises(ValueError, match="acceptance Evidence contract failed"):
        validator._validate_acceptance_evidence(
            experiment,
            candidate,
            experiment["splits"]["confirmation"],
        )


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
