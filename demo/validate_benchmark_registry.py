#!/usr/bin/env python3
"""Validate registered benchmark identities, artifacts, and public claims."""

from __future__ import annotations

import argparse
import hashlib
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

    suite_ids = [suite.get("suite_id") for suite in suites]
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
    evidence_count = 1
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
        "evidence_count": evidence_count,
        "qa_case_count": qa_case_count,
        "qa_case_types_present": sorted(qa_case_types),
        "suite_types": sorted(suite_types),
    }


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
