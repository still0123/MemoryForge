#!/usr/bin/env python3
"""Validate registered benchmark identities, artifacts, and public claims."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "demo/evaluation/registry.json"
SUITE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SPLITS = {"development", "confirmation", "holdout"}
SUITE_TYPES = {
    "document_wiki_qa",
    "code_wiki_structure",
    "code_wiki_qa",
    "source_lifecycle",
}
REQUIRED_EXPERIMENT_EVIDENCE_PATHS = {
    "exact-symbol-routing.learn-claude-code": {
        "demo/results/exact_symbol_routing_candidate_1_rejected.json",
        "demo/results/exact_symbol_routing_development.json",
        "demo/results/exact_symbol_routing_development_accepted.json",
        "demo/results/exact_symbol_routing_development_final.json",
    },
    "support-score.learn-claude-code": {
        "demo/results/support_score_development.json",
        "demo/results/support_score_development_final.json",
        "demo/results/support_score_development_candidate_3.json",
        "demo/results/support_score_development_candidate_4.json",
        "demo/results/support_score_development_candidate_5.json",
        "demo/results/support_score_development_candidate_6.json",
        "demo/results/support_score_development_candidate_7.json",
        "demo/results/support_score_development_candidate_8.json",
    },
}
_RESULTS = "demo/results/"
REQUIRED_ACCEPTANCE_EVIDENCE_PATHS = {
    "exact-symbol-routing.learn-claude-code": {
        _RESULTS + "exact_symbol_routing_development_final.json": (
            _RESULTS + "exact_symbol_routing_candidate_4_local_gate.json"
        ),
    },
    "support-score.learn-claude-code": {
        _RESULTS + f"support_score_development_candidate_{revision}.json": (
            _RESULTS + f"support_score_candidate_{revision}_local_gate.json"
        )
        for revision in (3, 4, 5, 6, 7, 8)
    },
}
DEVELOPMENT_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "source_manifest",
    "source_repository",
    "development",
    "baseline_evidence",
    "confirmation",
    "runs",
    "gates",
    "passed",
}
LOCAL_GATE_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "development_evidence",
    "local_gate",
    "confirmation",
    "passed",
}
REGRESSION_EVIDENCE_KEYS = {
    "schema_version",
    "suite_id",
    "suite_revision",
    "memoryforge_commit",
    "memoryforge_worktree_dirty",
    "development_evidence",
    "regression",
    "root_cause",
    "confirmation",
    "passed",
}
FINAL_EXPERIMENT_GATE_KEYS = {
    "exact-symbol-routing.learn-claude-code": {
        "answer_accuracy_at_least_90",
        "citation_grounding_accuracy",
        "confirmation_not_run",
        "deferred_abstention_gap_visible",
        "deterministic_replay",
        "exact_symbol_answer_accuracy",
        "fact_selection_accuracy",
        "multi_source_coverage",
        "page_route_recall_at_3",
        "repository_path_isolation_accuracy",
        "source_recall_at_3",
        "structural_benchmark",
    },
    "support-score.learn-claude-code": {
        "abstention_accuracy",
        "answer_accuracy",
        "citation_grounding_accuracy",
        "clean_source_worktree_after_run",
        "clean_worktree_after_run",
        "confirmation_not_run",
        "coverage",
        "deterministic_replay",
        "fact_selection_accuracy",
        "multi_source_coverage",
        "no_failed_cases",
        "page_route_recall_at_3",
        "per_case_support",
        "repository_path_isolation_accuracy",
        "risk",
        "selective_accuracy",
        "source_recall_at_3",
        "stable_memoryforge_commit",
        "stable_source_commit",
        "structural_benchmark",
        "unsupported_question_abstains",
    },
}
LOCAL_GATE_KEYS = {
    "command",
    "ruff_check",
    "ruff_format",
    "strict_mypy",
    "registry_validation",
    "dependency_check",
    "pytest",
    "wheel_clean_room",
    "sdist_clean_room",
    "pip_check",
    "cli_version_smoke",
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    summary = validate_registry(args.registry.resolve())
    print(json.dumps(summary, indent=2, sort_keys=True))


def validate_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, object]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("unsupported benchmark registry schema")
    if registry.get("package_release_target") != "0.3.0":
        raise ValueError("benchmark registry must target package 0.3.0")
    suites = registry.get("suites")
    if not isinstance(suites, list) or not suites:
        raise ValueError("benchmark registry requires suites")
    experiments = registry.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("benchmark registry requires experiments")

    suite_ids = [
        artifact.get("suite_id") for collection in (suites, experiments) for artifact in collection
    ]
    if len(suite_ids) != len(set(suite_ids)):
        raise ValueError("benchmark suite IDs must be unique")

    taxonomy_evidence = registry.get("taxonomy_evidence")
    if not isinstance(taxonomy_evidence, dict):
        raise ValueError("benchmark registry requires taxonomy evidence")
    _validate_artifact(taxonomy_evidence, "benchmark-taxonomy")
    if COMMIT.fullmatch(str(taxonomy_evidence.get("evaluation_commit"))) is None:
        raise ValueError("benchmark taxonomy Commit is invalid")
    taxonomy_payload = json.loads(
        (REPO_ROOT / taxonomy_evidence["path"]).read_text(encoding="utf-8")
    )
    if (
        taxonomy_payload.get("evaluation_commit") != taxonomy_evidence["evaluation_commit"]
        or taxonomy_payload.get("evaluation_worktree_dirty") is not False
        or taxonomy_payload.get("evaluated_case_count") != taxonomy_evidence["evaluated_case_count"]
        or taxonomy_payload.get("unrun_frozen_split", {}).get("status")
        != taxonomy_evidence["frozen_confirmation_status"]
    ):
        raise ValueError("benchmark taxonomy evidence contract failed")

    qa_case_count = 0
    qa_case_types: set[str] = set()
    registered_cases: dict[tuple[str, str], set[str]] = {}
    suite_types: set[str] = set()
    experiment_evidence_count = _validate_experiments(experiments)
    evidence_count = 1 + experiment_evidence_count
    for suite in suites:
        suite_id = suite.get("suite_id")
        if not isinstance(suite_id, str) or SUITE_ID.fullmatch(suite_id) is None:
            raise ValueError(f"invalid suite_id: {suite_id}")
        if re.search(r"(?:^|[._-])v\d", suite_id):
            raise ValueError(f"suite_id contains a package-like version: {suite_id}")
        revision = suite.get("suite_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"invalid suite_revision: {suite_id}")
        suite_type = suite.get("suite_type")
        if suite_type not in SUITE_TYPES:
            raise ValueError(f"invalid suite_type: {suite_id}")
        suite_types.add(suite_type)
        if suite.get("model_judge") is not False:
            raise ValueError(f"model judge must be disabled: {suite_id}")

        repositories = suite.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise ValueError(f"suite requires repositories: {suite_id}")
        for repository in repositories:
            _validate_repository(suite_id, repository)

        source_manifest = suite.get("source_manifest")
        if source_manifest is not None:
            _validate_artifact(source_manifest, suite_id)

        splits = suite.get("splits")
        if not isinstance(splits, dict) or set(splits) != SPLITS:
            raise ValueError(f"suite must declare all split keys: {suite_id}")
        for split_name, split in splits.items():
            if split is None:
                continue
            _validate_artifact(split, suite_id)
            case_count, case_types, case_ids = _suite_cases(split)
            if case_count != split.get("case_count"):
                raise ValueError(f"case count mismatch: {suite_id}/{split_name}")
            registered_cases[(suite_id, split_name)] = case_ids
            if suite_type in {"document_wiki_qa", "code_wiki_qa"}:
                qa_case_count += case_count
                qa_case_types.update(case_types)

        evidence = suite.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"suite requires generated evidence: {suite_id}")
        for artifact in evidence:
            _validate_artifact(artifact, suite_id)
            if artifact.get("split") not in SPLITS:
                raise ValueError(f"invalid evidence split: {suite_id}")
            evidence_revision = artifact.get("evidence_revision")
            if (
                isinstance(evidence_revision, bool)
                or not isinstance(evidence_revision, int)
                or evidence_revision < 1
            ):
                raise ValueError(f"invalid evidence revision: {suite_id}")
            if COMMIT.fullmatch(str(artifact.get("memoryforge_commit"))) is None:
                raise ValueError(f"invalid evidence Commit: {suite_id}")
            _validate_metrics(suite, artifact)
            evidence_count += 1

    if suite_types != SUITE_TYPES:
        raise ValueError("registry must contain all four benchmark suite types")
    overlay = registry.get("case_type_overlay")
    if not isinstance(overlay, dict):
        raise ValueError("benchmark registry requires a case type overlay")
    _validate_artifact(overlay, "benchmark-case-types")
    overlay_payload = json.loads((REPO_ROOT / overlay["path"]).read_text(encoding="utf-8"))
    mappings = overlay_payload.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("case type overlay requires mappings")
    mapping_keys = {
        (mapping.get("suite_id"), mapping.get("split"), mapping.get("case_id"))
        for mapping in mappings
    }
    if len(mapping_keys) != len(mappings):
        raise ValueError("case type overlay mappings must be unique")
    for mapping in mappings:
        key = (mapping.get("suite_id"), mapping.get("split"))
        if mapping.get("case_id") not in registered_cases.get(key, set()):
            raise ValueError("case type overlay references an unknown case")
        qa_case_types.add(str(mapping.get("case_type")))
    if qa_case_count != registry.get("qa_case_count") or not 100 <= qa_case_count <= 140:
        raise ValueError("registered QA case count must stay within 100-140")
    if qa_case_types != set(registry.get("qa_case_types_present", [])):
        raise ValueError("registered QA case types do not match suite contents")
    if qa_case_types != set(registry.get("qa_case_types_required", [])):
        raise ValueError("registered QA case types do not cover the required taxonomy")
    return {
        "status": "valid",
        "suite_count": len(suites),
        "experiment_count": len(experiments),
        "evidence_count": evidence_count,
        "qa_case_count": qa_case_count,
        "qa_case_types_present": sorted(qa_case_types),
        "suite_types": sorted(suite_types),
    }


def _validate_experiments(experiments: list[dict[str, Any]]) -> int:
    evidence_count = 0
    for experiment in experiments:
        suite_id = str(experiment.get("suite_id"))
        if SUITE_ID.fullmatch(suite_id) is None or re.search(r"(?:^|[._-])v\d", suite_id):
            raise ValueError(f"invalid experiment suite_id: {suite_id}")
        revision = experiment.get("suite_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"invalid experiment suite_revision: {suite_id}")
        if experiment.get("suite_type") not in SUITE_TYPES:
            raise ValueError(f"invalid experiment suite_type: {suite_id}")
        if experiment.get("model_judge") is not False:
            raise ValueError(f"experiment model judge must be disabled: {suite_id}")

        repositories = experiment.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise ValueError(f"experiment requires repositories: {suite_id}")
        for repository in repositories:
            _validate_repository(suite_id, repository)
        source_manifest = experiment.get("source_manifest")
        if not isinstance(source_manifest, dict):
            raise ValueError(f"experiment requires a source manifest: {suite_id}")
        _validate_artifact(source_manifest, suite_id)

        splits = experiment.get("splits")
        if not isinstance(splits, dict) or set(splits) != SPLITS:
            raise ValueError(f"experiment must declare all split keys: {suite_id}")
        development = splits["development"]
        confirmation = splits["confirmation"]
        if not isinstance(development, dict) or not isinstance(confirmation, dict):
            raise ValueError(f"experiment requires frozen development and confirmation: {suite_id}")
        _validate_artifact(development, suite_id)
        _validate_artifact(confirmation, suite_id)
        development_count, _, _ = _suite_cases(development)
        confirmation_count, _, _ = _suite_cases(confirmation)
        if (
            development_count != development.get("case_count")
            or confirmation_count != confirmation.get("case_count")
            or confirmation.get("status") != "not_run"
            or splits["holdout"] is not None
        ):
            raise ValueError(f"experiment split contract failed: {suite_id}")

        expected_metrics = experiment.get("expected_metrics")
        if not isinstance(expected_metrics, dict) or not isinstance(
            expected_metrics.get("development"), dict
        ):
            raise ValueError(f"experiment expected metrics missing: {suite_id}")
        evidence = experiment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"experiment requires generated evidence: {suite_id}")
        required_evidence_paths = REQUIRED_EXPERIMENT_EVIDENCE_PATHS.get(suite_id)
        evidence_paths = [artifact.get("path") for artifact in evidence]
        if (
            required_evidence_paths is None
            or len(evidence_paths) != len(set(evidence_paths))
            or set(evidence_paths) != required_evidence_paths
        ):
            raise ValueError(f"experiment Evidence history is incomplete: {suite_id}")
        required_statuses = experiment.get("required_evidence_statuses")
        allowed_statuses = {
            "rejected",
            "development_passed_regression_failed",
            "accepted_development_superseded",
            "accepted_development",
        }
        if (
            not isinstance(required_statuses, list)
            or not required_statuses
            or any(status not in allowed_statuses for status in required_statuses)
            or len(required_statuses) != len(set(required_statuses))
            or "accepted_development" not in required_statuses
            or not {"rejected", "development_passed_regression_failed"} & set(required_statuses)
        ):
            raise ValueError(f"invalid required experiment Evidence statuses: {suite_id}")
        revisions: set[int] = set()
        statuses: set[str] = set()
        required_acceptance = REQUIRED_ACCEPTANCE_EVIDENCE_PATHS.get(suite_id)
        if required_acceptance is None:
            raise ValueError(f"experiment acceptance Evidence history is missing: {suite_id}")
        for artifact in evidence:
            _validate_artifact(artifact, suite_id)
            evidence_revision = artifact.get("evidence_revision")
            if (
                isinstance(evidence_revision, bool)
                or not isinstance(evidence_revision, int)
                or evidence_revision < 1
                or evidence_revision in revisions
            ):
                raise ValueError(f"invalid experiment evidence revision: {suite_id}")
            revisions.add(evidence_revision)
            status = str(artifact.get("status"))
            if status not in allowed_statuses:
                raise ValueError(f"invalid experiment evidence status: {suite_id}")
            expected_acceptance_path = required_acceptance.get(str(artifact.get("path")))
            acceptance_evidence = artifact.get("acceptance_evidence")
            if expected_acceptance_path is None:
                if acceptance_evidence is not None:
                    raise ValueError(f"unexpected experiment acceptance Evidence: {suite_id}")
            elif (
                not isinstance(acceptance_evidence, dict)
                or acceptance_evidence.get("path") != expected_acceptance_path
            ):
                raise ValueError(
                    f"experiment acceptance Evidence history is incomplete: {suite_id}"
                )
            statuses.add(status)
            commit = str(artifact.get("memoryforge_commit"))
            if COMMIT.fullmatch(commit) is None:
                raise ValueError(f"invalid experiment Evidence Commit: {suite_id}")
            payload = cast(
                dict[str, Any],
                json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
            )
            _validate_experiment_payload(
                experiment,
                artifact,
                payload,
                development,
                confirmation,
            )
            if status == "development_passed_regression_failed":
                evidence_count += _validate_regression_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            if expected_acceptance_path is not None:
                evidence_count += _validate_acceptance_evidence(
                    experiment,
                    artifact,
                    confirmation,
                )
            evidence_count += 1
        if statuses != set(required_statuses):
            raise ValueError(f"experiment must retain rejected and accepted Evidence: {suite_id}")
    return evidence_count


def _validate_experiment_payload(
    experiment: dict[str, Any],
    artifact: dict[str, Any],
    payload: dict[str, Any],
    development: dict[str, Any],
    confirmation: dict[str, Any],
) -> None:
    repository = experiment["repositories"][0]
    if (
        payload.get("schema_version") != 1
        or set(payload) != DEVELOPMENT_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != artifact["memoryforge_commit"]
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("passed") is not artifact["passed"]
        or payload.get("source_manifest") != experiment["source_manifest"]
        or payload.get("source_repository", {}).get("commit") != repository["commit"]
        or payload.get("development", {}).get("path") != development["path"]
        or payload.get("development", {}).get("sha256") != development["sha256"]
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
    ):
        raise ValueError(f"experiment Evidence contract failed: {experiment['suite_id']}")
    if artifact["status"] == "rejected":
        if artifact["passed"] is not False:
            raise ValueError("rejected experiment Evidence must fail")
        return
    if artifact["passed"] is not True:
        raise ValueError("accepted experiment Evidence must pass")
    if artifact["status"] == "accepted_development":
        gates = payload.get("gates")
        required_gates = FINAL_EXPERIMENT_GATE_KEYS.get(str(experiment["suite_id"]))
        if (
            required_gates is None
            or not isinstance(gates, dict)
            or set(gates) != required_gates
            or not all(value is True for value in gates.values())
        ):
            raise ValueError(f"experiment Evidence gates failed: {experiment['suite_id']}")
        if experiment["suite_id"] == "support-score.learn-claude-code":
            source_manifest = json.loads(
                (REPO_ROOT / experiment["source_manifest"]["path"]).read_text(encoding="utf-8")
            )
            threshold = source_manifest.get("support_threshold")
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or payload.get("development", {}).get("support_threshold") != threshold
            ):
                raise ValueError("support-score threshold does not match frozen manifest")
            cases = payload.get("development", {}).get("evaluation", {}).get("cases")
            if not isinstance(cases, list) or len(cases) != development["case_count"]:
                raise ValueError("support-score case Evidence is incomplete")
            for case in cases:
                if not _support_benchmark_module()._valid_case_support(
                    case,
                    float(threshold),
                ):
                    raise ValueError("support-score case Evidence contract failed")
    actual = payload["development"]["metrics"]
    for metric, expected in experiment["expected_metrics"]["development"].items():
        if actual.get(metric) != expected:
            raise ValueError(
                f"experiment metric mismatch: {experiment['suite_id']}/development/{metric}"
            )


def _validate_regression_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("regression_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("regression-rejected experiment requires regression Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if COMMIT.fullmatch(commit) is None or artifact.get("passed") is not False:
        raise ValueError("invalid experiment regression Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    pytest_result = payload.get("regression", {}).get("pytest", {})
    if (
        payload.get("schema_version") != 1
        or set(payload) != REGRESSION_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("development_evidence", {}).get("path") != development_artifact["path"]
        or payload.get("development_evidence", {}).get("sha256") != development_artifact["sha256"]
        or payload.get("development_evidence", {}).get("memoryforge_commit")
        != development_artifact["memoryforge_commit"]
        or payload.get("development_evidence", {}).get("passed") is not True
        or not isinstance(pytest_result.get("failed"), int)
        or pytest_result["failed"] < 1
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
        or payload.get("passed") is not False
    ):
        raise ValueError(
            f"experiment regression Evidence contract failed: {experiment['suite_id']}"
        )
    return 1


def _validate_acceptance_evidence(
    experiment: dict[str, Any],
    development_artifact: dict[str, Any],
    confirmation: dict[str, Any],
) -> int:
    artifact = development_artifact.get("acceptance_evidence")
    if not isinstance(artifact, dict):
        raise ValueError("accepted experiment requires local gate Evidence")
    _validate_artifact(artifact, str(experiment["suite_id"]))
    commit = str(artifact.get("memoryforge_commit"))
    if COMMIT.fullmatch(commit) is None or artifact.get("passed") is not True:
        raise ValueError("invalid experiment acceptance Evidence identity")
    payload = cast(
        dict[str, Any],
        json.loads((REPO_ROOT / artifact["path"]).read_text(encoding="utf-8")),
    )
    local_gate = payload.get("local_gate", {})
    pytest_result = local_gate.get("pytest", {})
    registry_result = local_gate.get("registry_validation", {})
    artifacts = local_gate.get("artifacts")
    requires_artifacts = experiment["suite_id"] == "support-score.learn-claude-code"
    expected_local_gate_keys = LOCAL_GATE_KEYS | ({"artifacts"} if requires_artifacts else set())
    if (
        payload.get("schema_version") != 1
        or set(payload) != LOCAL_GATE_EVIDENCE_KEYS
        or payload.get("suite_id") != experiment["suite_id"]
        or payload.get("suite_revision") != experiment["suite_revision"]
        or payload.get("memoryforge_commit") != commit
        or payload.get("memoryforge_worktree_dirty") is not False
        or payload.get("development_evidence", {}).get("path") != development_artifact["path"]
        or payload.get("development_evidence", {}).get("sha256") != development_artifact["sha256"]
        or payload.get("development_evidence", {}).get("memoryforge_commit")
        != development_artifact["memoryforge_commit"]
        or payload.get("development_evidence", {}).get("passed") is not True
        or not isinstance(local_gate, dict)
        or set(local_gate) != expected_local_gate_keys
        or local_gate.get("command") != "scripts/check_local.sh"
        or local_gate.get("ruff_check") != "passed"
        or local_gate.get("ruff_format") != "passed"
        or local_gate.get("strict_mypy") != "passed"
        or not isinstance(registry_result, dict)
        or set(registry_result)
        != {"suite_count", "experiment_count", "evidence_count", "qa_case_count"}
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in registry_result.values()
        )
        or local_gate.get("dependency_check") != "passed"
        or not isinstance(pytest_result.get("passed"), int)
        or isinstance(pytest_result.get("passed"), bool)
        or pytest_result["passed"] < 1
        or not isinstance(pytest_result.get("failed"), int)
        or isinstance(pytest_result.get("failed"), bool)
        or pytest_result.get("failed") != 0
        or not isinstance(pytest_result.get("coverage_percent"), int)
        or isinstance(pytest_result.get("coverage_percent"), bool)
        or not 0 <= pytest_result["coverage_percent"] <= 100
        or local_gate.get("wheel_clean_room") != "passed"
        or local_gate.get("sdist_clean_room") != "passed"
        or local_gate.get("pip_check") != "passed"
        or local_gate.get("cli_version_smoke") != "passed"
        or (
            requires_artifacts
            and (
                not isinstance(artifacts, dict)
                or set(artifacts)
                != {
                    "wheel_sha256",
                    "sdist_sha256",
                    "provenance_sha256",
                    "sha256sums_sha256",
                }
                or any(SHA256.fullmatch(str(value)) is None for value in artifacts.values())
            )
        )
        or payload.get("confirmation", {}).get("path") != confirmation["path"]
        or payload.get("confirmation", {}).get("sha256") != confirmation["sha256"]
        or payload.get("confirmation", {}).get("status") != "not_run"
        or payload.get("passed") is not True
    ):
        raise ValueError(
            f"experiment acceptance Evidence contract failed: {experiment['suite_id']}"
        )
    return 1


def _support_benchmark_module() -> Any:
    script = REPO_ROOT / "demo/run_support_score_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_support_score_benchmark", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load support-score benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_repository(suite_id: str, repository: dict[str, Any]) -> None:
    if not repository.get("repository") or not repository.get("remote_url"):
        raise ValueError(f"repository identity missing: {suite_id}")
    if COMMIT.fullmatch(str(repository.get("commit"))) is None:
        raise ValueError(f"repository Commit is not frozen: {suite_id}")
    if not repository.get("license"):
        raise ValueError(f"repository license missing: {suite_id}")
    source_paths = repository.get("source_paths")
    if (
        not isinstance(source_paths, list)
        or not source_paths
        or any(not isinstance(path, str) or not path for path in source_paths)
    ):
        raise ValueError(f"repository source paths missing: {suite_id}")


def _validate_artifact(artifact: dict[str, Any], suite_id: str) -> None:
    path = REPO_ROOT / str(artifact.get("path", ""))
    if not path.is_file() or not path.is_relative_to(REPO_ROOT):
        raise ValueError(f"registered artifact missing: {suite_id}")
    expected_sha = str(artifact.get("sha256"))
    if SHA256.fullmatch(expected_sha) is None:
        raise ValueError(f"registered artifact SHA256 invalid: {suite_id}")
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(f"registered artifact SHA256 mismatch: {suite_id}")


def _suite_cases(split: dict[str, Any]) -> tuple[int, set[str], set[str]]:
    artifact = json.loads((REPO_ROOT / split["path"]).read_text(encoding="utf-8"))
    if "cases" in artifact and isinstance(artifact["cases"], list):
        return (
            len(artifact["cases"]),
            {str(case["category"]) for case in artifact["cases"] if "category" in case},
            {str(case["id"]) for case in artifact["cases"]},
        )
    repositories = artifact.get("repositories")
    if isinstance(repositories, list) and all(
        isinstance(repository.get("expected_source_count"), int) for repository in repositories
    ):
        return (
            sum(int(repository["expected_source_count"]) for repository in repositories),
            set(),
            set(),
        )
    source_count = len(artifact["expected_source_paths"])
    return (
        source_count
        + len(artifact["symbols"])
        + len(artifact["relations"])
        + len(artifact["modules"]),
        set(),
        set(),
    )


def _validate_metrics(suite: dict[str, Any], evidence: dict[str, Any]) -> None:
    split = str(evidence["split"])
    expected = suite["expected_metrics"].get(split)
    if not isinstance(expected, dict):
        raise ValueError(f"expected metrics missing: {suite['suite_id']}/{split}")
    payload = json.loads((REPO_ROOT / evidence["path"]).read_text(encoding="utf-8"))
    actual = _evidence_metrics(payload, suite["repositories"][0]["commit"])
    for metric, value in expected.items():
        if isinstance(value, (int, float)) and actual.get(metric) != value:
            raise ValueError(f"evidence metric mismatch: {suite['suite_id']}/{split}/{metric}")


def _evidence_metrics(payload: dict[str, Any], repository_commit: str) -> dict[str, Any]:
    if isinstance(payload.get("evaluation"), dict) and isinstance(
        payload["evaluation"].get("memoryforge"), dict
    ):
        return cast(dict[str, Any], payload["evaluation"]["memoryforge"])
    if isinstance(payload.get("memoryforge"), dict):
        return cast(dict[str, Any], payload["memoryforge"])
    if isinstance(payload.get("metrics"), dict):
        return cast(dict[str, Any], payload["metrics"])
    if isinstance(payload.get("gates"), dict):
        return {
            **cast(dict[str, Any], payload.get("counts", {})),
            **cast(dict[str, Any], payload["gates"]),
            "passed": payload.get("passed"),
        }
    for repository in payload.get("repositories", []):
        if repository.get("commit") == repository_commit:
            return cast(dict[str, Any], repository["evaluation"]["metrics"])
    raise ValueError("could not locate evidence metrics")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
