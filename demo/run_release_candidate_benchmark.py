#!/usr/bin/env python3
"""Evaluate the frozen v0.3.0 release-candidate development contract."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src"
DEVELOPMENT = REPO_ROOT / "demo/evaluation/release_candidate_development.json"
CONFIRMATION = REPO_ROOT / "demo/evaluation/release_candidate_confirmation.json"
HOLDOUT = REPO_ROOT / "demo/evaluation/release_candidate_holdout.json"
DEVELOPMENT_SHA256 = "8d9fe33359b71ac0b86b6fa42b0bc5ee34080126af3ae0b9bf2fa2c0121cf2a6"
CONFIRMATION_SHA256 = "3eed08e04592b614a709cd18c6a92951b5da145d7dab4b9997824cd4e002e805"
HOLDOUT_SHA256 = "b466644bef2748a96d598540c54633fe49770af97fc71ce6e62e18eda687e8f8"
TARGET_VERSION = "0.3.0"
SHA256 = re.compile(r"^[a-f0-9]{64}$")
RESULT_PATHS = (
    REPO_ROOT / "demo/results/release_candidate_confirmation.json",
    REPO_ROOT / "demo/results/release_candidate_holdout.json",
)
DOCUMENTS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "docs/EVIDENCE_CLAIMS.md",
    REPO_ROOT / "docs/PORTFOLIO_DEMO.md",
    REPO_ROOT / "docs/V030_RELEASE_CANDIDATE_SPEC.md",
)
DOCUMENT_CLAIMS = {
    DOCUMENTS[0]: (
        "v0.3.0",
        "10% / 0%",
        "Candidate 11 development",
        "609 passed",
        "606 passed / 3 skipped",
        "原生 Windows confirmation 未运行",
    ),
    DOCUMENTS[1]: (
        "v0.3.0 — Release Candidate",
        "Candidate 11 development",
        "609 passed",
        "606 passed / 3 skipped",
        "原生 Windows confirmation",
        "单次 holdout",
    ),
    DOCUMENTS[2]: (
        "v0.3.0 RC",
        "10%/0%",
        "Candidate 11 development",
        "609 passed",
        "606 passed / 3 skipped",
        "不等于原生 Windows confirmation",
    ),
    DOCUMENTS[3]: (
        "release candidate",
        "10%/0%",
        "Candidate 11",
        "609 passed",
        "606 passed / 3 skipped",
        "原生 Windows confirmation",
    ),
    DOCUMENTS[4]: (
        "LOCAL_GATES_PASSED_REVIEW_REJECTED",
        "609 passed",
        "606 passed, 3 skipped",
        "Confirmation status: `not_run`",
        "Holdout status: `not_run`",
    ),
}
RELEASE_CLAIM_MARKER = (
    "<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; "
    "active_candidate=11; platform_gate_candidate=11; platform_gate_status=accepted; "
    "review_status=rejected; "
    "macos_passed=609; linux_passed=606; linux_skipped=3; "
    "windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->"
)
FORBIDDEN_RELEASE_CLAIMS = (
    "v0.3.0 已发布",
    "v0.3.0 is released",
    "原生 Windows confirmation 已完成",
    "Windows confirmation passed",
    "v0.3.0 holdout 已完成",
)
FORBIDDEN_COMPLETION_PATTERN = re.compile(
    r"(?i)(?:"
    r"v0\.3\.0[^\n]{0,120}(?:confirmation|holdout)[^\n]{0,80}"
    r"(?:passed|succeeded|completed)"
    r"|(?:confirmation|holdout)[^\n]{0,80}(?:passed|succeeded|completed)"
    r"[^\n]{0,120}v0\.3\.0"
    r")"
)
WHEEL_CLEAN_ROOM_CHECKS = {
    "pip_check": "passed",
    "cli_help": "passed",
    "cli_version": "passed",
    "code_wiki_benchmark": "passed",
    "public_demo": "not_run",
}
SDIST_CLEAN_ROOM_CHECKS = {
    "install": "passed",
    "pip_check": "passed",
    "import": "passed",
    "cli_version": "passed",
}
_REGISTRY_SCRIPT = REPO_ROOT / "demo/validate_benchmark_registry.py"
_REGISTRY_SPEC = importlib.util.spec_from_file_location(
    "validate_benchmark_registry",
    _REGISTRY_SCRIPT,
)
if _REGISTRY_SPEC is None or _REGISTRY_SPEC.loader is None:
    raise RuntimeError("could not load benchmark registry validator")
registry_validator = importlib.util.module_from_spec(_REGISTRY_SPEC)
_REGISTRY_SPEC.loader.exec_module(registry_validator)
_SUMMARY_SCRIPT = REPO_ROOT / "demo/build_benchmark_summary.py"
_SUMMARY_SPEC = importlib.util.spec_from_file_location(
    "build_benchmark_summary",
    _SUMMARY_SCRIPT,
)
if _SUMMARY_SPEC is None or _SUMMARY_SPEC.loader is None:
    raise RuntimeError("could not load benchmark summary builder")
summary_builder = importlib.util.module_from_spec(_SUMMARY_SPEC)
_SUMMARY_SPEC.loader.exec_module(summary_builder)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    release_dir = args.release_dir.resolve()
    output = args.output.resolve()
    if output.is_relative_to(REPO_ROOT) or release_dir.is_relative_to(REPO_ROOT):
        raise SystemExit("release inputs and output must remain outside the repository")
    if output == release_dir or output.is_relative_to(release_dir):
        raise SystemExit("--output must remain outside --release-dir")
    release_dir.mkdir(parents=True, exist_ok=True)
    memoryforge_commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    development, confirmation, holdout = _load_frozen_inputs()

    first = _run_suite(development, release_dir, confirmation, holdout)
    second = _run_suite(development, release_dir, confirmation, holdout)
    runs = [
        {"name": "first", "evaluation_sha256": _payload_sha256(first)},
        {"name": "second", "evaluation_sha256": _payload_sha256(second)},
    ]
    metrics = cast(dict[str, object], first["metrics"])
    gates = {
        "pass_rate": metrics["pass_rate"] == 100.0,
        "failed_cases": metrics["failed_cases"] == 0,
        "reproducible_artifacts": metrics["reproducible_artifacts"] is True,
        "private_detail_leaks": metrics["private_detail_leaks"] == 0,
        "confirmation_not_run": metrics["confirmation_not_run"] is True,
        "holdout_not_run": metrics["holdout_not_run"] is True,
        "deterministic_replay": runs[0]["evaluation_sha256"] == runs[1]["evaluation_sha256"],
        "stable_memoryforge_commit": _git("rev-parse", "HEAD") == memoryforge_commit,
        "clean_worktree_after_run": not bool(_git("status", "--porcelain")),
    }
    evidence = {
        "schema_version": 1,
        "suite_id": development["suite_id"],
        "suite_revision": development["suite_revision"],
        "memoryforge_commit": memoryforge_commit,
        "memoryforge_worktree_dirty": False,
        "development": {
            "path": str(DEVELOPMENT.relative_to(REPO_ROOT)),
            "sha256": DEVELOPMENT_SHA256,
            "case_count": len(development["cases"]),
            "evaluation": first,
        },
        "confirmation": {
            "path": str(CONFIRMATION.relative_to(REPO_ROOT)),
            "sha256": CONFIRMATION_SHA256,
            "status": "not_run",
        },
        "holdout": {
            "path": str(HOLDOUT.relative_to(REPO_ROOT)),
            "sha256": HOLDOUT_SHA256,
            "status": "not_run",
        },
        "release_artifacts": _release_artifact_evidence(
            release_dir,
            memoryforge_commit,
        ),
        "runs": runs,
        "gates": gates,
        "passed": all(gates.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote release-candidate development Evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("release-candidate development gate failed")


def _run_suite(
    development: dict[str, Any],
    release_dir: Path,
    confirmation: dict[str, Any],
    holdout: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "package-version-consistency": _check_versions(release_dir),
        "registry-and-benchmark-summary": _check_benchmark_summary(release_dir),
        "local-reproducible-artifacts": _check_reproducible_artifacts(release_dir),
        "workspace-release-drill": _check_workspace_drill(release_dir),
        "release-document-consistency": _check_documents(release_dir),
        "frozen-splits-remain-closed": _check_splits_closed(confirmation, holdout),
    }
    cases = [
        {
            "id": case["id"],
            "status": "passed" if checks[case["id"]]["passed"] else "failed",
            "error_classification": checks[case["id"]]["error_classification"],
        }
        for case in development["cases"]
    ]
    failed = sum(case["status"] == "failed" for case in cases)
    provenance = _load_json(release_dir / "release-provenance.json")
    private_detail_leaks = _private_detail_leaks(
        [
            provenance,
            _load_json(release_dir / "benchmark-summary.json"),
            _load_json(release_dir / "workspace-drill.json"),
        ]
    )
    return {
        "case_count": len(cases),
        "metrics": {
            "pass_rate": round(100 * (len(cases) - failed) / len(cases), 1),
            "failed_cases": failed,
            "reproducible_artifacts": checks["local-reproducible-artifacts"]["passed"],
            "private_detail_leaks": private_detail_leaks,
            "confirmation_not_run": confirmation["status"] == "not_run"
            and not RESULT_PATHS[0].exists(),
            "holdout_not_run": holdout["status"] == "not_run" and not RESULT_PATHS[1].exists(),
        },
        "cases": cases,
    }


def _check_versions(release_dir: Path) -> dict[str, object]:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    provenance = _load_json(release_dir / "release-provenance.json")
    package = provenance.get("package")
    artifacts = _artifact_paths(release_dir, package)
    checks = provenance.get("checks")
    if (
        not _release_provenance_valid(provenance)
        or not isinstance(package, dict)
        or artifacts is None
        or not isinstance(checks, dict)
        or not _clean_room_passed(checks.get("wheel_clean_room"), WHEEL_CLEAN_ROOM_CHECKS)
        or not _clean_room_passed(checks.get("sdist_clean_room"), SDIST_CLEAN_ROOM_CHECKS)
    ):
        return _check(False, "version_mismatch")
    wheel, sdist = artifacts
    wheel_name, wheel_version = _wheel_metadata(wheel)
    sdist_name, sdist_version = _sdist_metadata(sdist)
    versions = {
        str(project.get("project", {}).get("version", "")),
        _source_module_version(),
        _cli_version(),
        str(package.get("version", "")),
        wheel_version,
        sdist_version,
    }
    passed = versions == {TARGET_VERSION} and wheel_name == sdist_name == "memoryforge"
    return _check(passed, "none" if passed else "version_mismatch")


def _check_benchmark_summary(release_dir: Path) -> dict[str, object]:
    summary = _load_json(release_dir / "benchmark-summary.json")
    expected = summary_builder.build_summary(
        memoryforge_commit=_git("rev-parse", "HEAD"),
    )
    passed = summary == expected
    return _check(passed, "none" if passed else "benchmark_summary_mismatch")


def _check_reproducible_artifacts(release_dir: Path) -> dict[str, object]:
    entries = list(release_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        return _check(False, "artifact_contract_invalid")
    provenance = _load_json(release_dir / "release-provenance.json")
    package = provenance.get("package")
    builds = provenance.get("builds")
    artifacts = _artifact_paths(release_dir, package)
    if artifacts is None:
        return _check(False, "artifact_missing")
    if not _release_provenance_valid(provenance):
        return _check(False, "artifact_contract_invalid")
    sums = _read_sha256sums(release_dir)
    if sums is None:
        classification = (
            "artifact_missing"
            if not (release_dir / "SHA256SUMS").is_file()
            else "artifact_contract_invalid"
        )
        return _check(False, classification)
    if not isinstance(builds, list) or len(builds) != 2:
        return _check(False, "artifact_contract_invalid")
    wheel_path, sdist_path = artifacts
    actual = {
        "wheel": {
            "path": wheel_path.name,
            "sha256": _sha256(wheel_path),
            "size": wheel_path.stat().st_size,
        },
        "sdist": {
            "path": sdist_path.name,
            "sha256": _sha256(sdist_path),
            "size": sdist_path.stat().st_size,
        },
    }
    identities = []
    retained_paths: set[Path] = set()
    for index, build in enumerate(builds):
        if not isinstance(build, dict) or build.get("name") != ("first", "second")[index]:
            return _check(False, "artifact_contract_invalid")
        wheel = build.get("wheel")
        sdist = build.get("sdist")
        if not isinstance(wheel, dict) or not isinstance(sdist, dict):
            return _check(False, "artifact_contract_invalid")
        for kind, record in (("wheel", wheel), ("sdist", sdist)):
            if (
                set(record) != {"path", "sha256", "size", "retained_path"}
                or {key: record[key] for key in ("path", "sha256", "size")} != actual[kind]
            ):
                return _check(False, "artifact_reproducibility_failure")
            retained_name = record.get("retained_path")
            if not isinstance(retained_name, str):
                return _check(False, "artifact_contract_invalid")
            retained = (release_dir / retained_name).resolve()
            if (
                not retained.is_relative_to(release_dir.resolve())
                or retained in retained_paths
                or not retained.is_file()
                or _sha256(retained) != record["sha256"]
                or retained.stat().st_size != record["size"]
            ):
                return _check(False, "artifact_reproducibility_failure")
            retained_paths.add(retained)
        identities.append((wheel.get("sha256"), sdist.get("sha256")))
    expected_files = {
        "benchmark-summary.json",
        "release-provenance.json",
        "workspace-drill.json",
        wheel_path.name,
        sdist_path.name,
        *(
            str(record["retained_path"])
            for build in builds
            for record in (build["wheel"], build["sdist"])
        ),
    }
    actual_files = {path.name for path in entries if path.name != "SHA256SUMS"}
    passed = (
        identities[0] == identities[1]
        and set(sums) == expected_files
        and actual_files == expected_files
        and all(SHA256.fullmatch(str(value)) is not None for value in identities[0])
        and isinstance(package, dict)
        and package.get("version") == TARGET_VERSION
        and package.get("wheel_sha256") == actual["wheel"]["sha256"]
        and package.get("sdist_sha256") == actual["sdist"]["sha256"]
        and provenance.get("memoryforge_commit") == _git("rev-parse", "HEAD")
        and provenance.get("memoryforge_worktree_dirty") is False
        and provenance.get("reproducible_artifacts") is True
    )
    return _check(passed, "none" if passed else "artifact_reproducibility_failure")


def _check_workspace_drill(release_dir: Path) -> dict[str, object]:
    drill = _load_json(release_dir / "workspace-drill.json")
    passed = registry_validator._release_drill_contract(
        drill,
        _git("rev-parse", "HEAD"),
        evidence_revision=11,
    )
    return _check(passed, "none" if passed else "workspace_drill_failure")


def _check_documents(release_dir: Path) -> dict[str, object]:
    provenance = _load_json(release_dir / "release-provenance.json")
    summary = _load_json(release_dir / "benchmark-summary.json")
    drill = _load_json(release_dir / "workspace-drill.json")
    package = provenance.get("package")
    checks = provenance.get("checks")
    texts = {path: path.read_text(encoding="utf-8") for path in DOCUMENTS}
    passed = (
        all(
            all(claim in texts[path] for claim in claims)
            for path, claims in DOCUMENT_CLAIMS.items()
        )
        and _document_claims_consistent(texts)
        and _release_provenance_valid(provenance)
        and isinstance(package, dict)
        and package.get("version") == TARGET_VERSION
        and isinstance(checks, dict)
        and checks.get("workspace_drill") == "passed"
        and checks.get("benchmark_summary") == "passed"
        and checks.get("sdist_members") == "passed"
        and _clean_room_passed(checks.get("wheel_clean_room"), WHEEL_CLEAN_ROOM_CHECKS)
        and _clean_room_passed(checks.get("sdist_clean_room"), SDIST_CLEAN_ROOM_CHECKS)
        and provenance.get("memoryforge_commit") == _git("rev-parse", "HEAD")
        and provenance.get("confirmation") == {"status": "not_run"}
        and provenance.get("holdout") == {"status": "not_run"}
        and summary.get("package_version") == TARGET_VERSION
        and summary.get("memoryforge_commit") == _git("rev-parse", "HEAD")
        and summary.get("registry") == registry_validator.validate_registry()
        and drill.get("memoryforge_commit") == _git("rev-parse", "HEAD")
        and drill.get("passed") is True
    )
    return _check(passed, "none" if passed else "release_document_mismatch")


def _artifact_paths(
    release_dir: Path,
    package: object,
) -> tuple[Path, Path] | None:
    if not isinstance(package, dict):
        return None
    names = (package.get("wheel"), package.get("sdist"))
    if names != (
        f"memoryforge-{TARGET_VERSION}-py3-none-any.whl",
        f"memoryforge-{TARGET_VERSION}.tar.gz",
    ):
        return None
    wheel = release_dir / str(names[0])
    sdist = release_dir / str(names[1])
    if not wheel.is_file() or not sdist.is_file():
        return None
    return wheel, sdist


def _document_claims_consistent(texts: dict[Path, str]) -> bool:
    if not all(text.count(RELEASE_CLAIM_MARKER) == 1 for text in texts.values()):
        return False
    for text in texts.values():
        if any(forbidden in text for forbidden in FORBIDDEN_RELEASE_CLAIMS):
            return False
        if FORBIDDEN_COMPLETION_PATTERN.search(text):
            return False
        statuses = re.findall(
            r"(?i)(?:confirmation|holdout)\s+status\s*:\s*`?([a-z_]+)",
            text,
        )
        if any(status != "not_run" for status in statuses):
            return False
    return True


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            expected = f"memoryforge-{TARGET_VERSION}.dist-info/METADATA"
            if archive.namelist().count(expected) != 1:
                return "", ""
            metadata = BytesParser(policy=default).parsebytes(archive.read(expected))
    except (OSError, KeyError, zipfile.BadZipFile):
        return "", ""
    return str(metadata.get("Name", "")).casefold(), str(metadata.get("Version", ""))


def _sdist_metadata(path: Path) -> tuple[str, str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            expected = f"memoryforge-{TARGET_VERSION}/PKG-INFO"
            members = [member for member in archive.getmembers() if member.name == expected]
            if len(members) != 1:
                return "", ""
            extracted = archive.extractfile(members[0])
            if extracted is None:
                return "", ""
            metadata = BytesParser(policy=default).parsebytes(extracted.read())
    except (OSError, tarfile.TarError):
        return "", ""
    return str(metadata.get("Name", "")).casefold(), str(metadata.get("Version", ""))


def _release_provenance_valid(provenance: object) -> bool:
    if not isinstance(provenance, dict):
        return False
    package = provenance.get("package")
    builds = provenance.get("builds")
    runtime = provenance.get("runtime")
    checks = provenance.get("checks")
    dependencies = provenance.get("dependencies")
    if (
        set(provenance)
        != {
            "schema_version",
            "memoryforge_commit",
            "memoryforge_worktree_dirty",
            "package",
            "builds",
            "reproducible_artifacts",
            "runtime",
            "checks",
            "dependencies",
            "confirmation",
            "holdout",
        }
        or type(provenance.get("schema_version")) is not int
        or provenance.get("schema_version") != 1
        or provenance.get("memoryforge_commit") != _git("rev-parse", "HEAD")
        or provenance.get("memoryforge_worktree_dirty") is not False
        or provenance.get("reproducible_artifacts") is not True
        or not isinstance(package, dict)
        or set(package)
        != {
            "version",
            "wheel",
            "wheel_sha256",
            "sdist",
            "sdist_sha256",
        }
        or package.get("version") != TARGET_VERSION
        or package.get("wheel") != f"memoryforge-{TARGET_VERSION}-py3-none-any.whl"
        or package.get("sdist") != f"memoryforge-{TARGET_VERSION}.tar.gz"
        or SHA256.fullmatch(str(package.get("wheel_sha256"))) is None
        or SHA256.fullmatch(str(package.get("sdist_sha256"))) is None
        or not isinstance(builds, list)
        or len(builds) != 2
        or not isinstance(runtime, dict)
        or set(runtime) != {"implementation", "python", "system", "machine"}
        or runtime.get("implementation") != "CPython"
        or not str(runtime.get("python", "")).startswith("3.11.")
        or not all(
            isinstance(runtime.get(key), str) and runtime[key] for key in ("system", "machine")
        )
        or not isinstance(checks, dict)
        or set(checks)
        != {
            "wheel_clean_room",
            "sdist_clean_room",
            "workspace_drill",
            "benchmark_summary",
            "sdist_members",
        }
        or not _clean_room_passed(checks.get("wheel_clean_room"), WHEEL_CLEAN_ROOM_CHECKS)
        or not _clean_room_passed(checks.get("sdist_clean_room"), SDIST_CLEAN_ROOM_CHECKS)
        or any(
            checks.get(key) != "passed"
            for key in ("workspace_drill", "benchmark_summary", "sdist_members")
        )
        or not isinstance(dependencies, dict)
        or not dependencies
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in dependencies.items()
        )
        or provenance.get("confirmation") != {"status": "not_run"}
        or provenance.get("holdout") != {"status": "not_run"}
    ):
        return False
    for index, build in enumerate(builds):
        if not isinstance(build, dict) or set(build) != {"name", "wheel", "sdist"}:
            return False
        if build.get("name") != ("first", "second")[index]:
            return False
        for kind in ("wheel", "sdist"):
            record = build.get(kind)
            if (
                not isinstance(record, dict)
                or set(record) != {"path", "sha256", "size", "retained_path"}
                or record.get("path") != package.get(kind)
                or SHA256.fullmatch(str(record.get("sha256"))) is None
                or type(record.get("size")) is not int
                or record["size"] < 1
                or not isinstance(record.get("retained_path"), str)
            ):
                return False
    return True


def _read_sha256sums(root: Path) -> dict[str, str] | None:
    path = root / "SHA256SUMS"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    sums: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2:
            return None
        digest, relative = parts
        artifact = (root / relative).resolve()
        if (
            SHA256.fullmatch(digest) is None
            or relative in sums
            or not artifact.is_relative_to(root.resolve())
            or not artifact.is_file()
            or _sha256(artifact) != digest
        ):
            return None
        sums[relative] = digest
    return sums


def _clean_room_passed(value: object, expected: dict[str, str]) -> bool:
    return isinstance(value, dict) and value == expected


def _check_splits_closed(
    confirmation: dict[str, Any],
    holdout: dict[str, Any],
) -> dict[str, object]:
    workflows = REPO_ROOT / ".github/workflows"
    hosted_workflows = (
        [*workflows.glob("*.yml"), *workflows.glob("*.yaml")] if workflows.is_dir() else []
    )
    passed = (
        confirmation["status"] == "not_run"
        and holdout["status"] == "not_run"
        and not any(path.exists() for path in RESULT_PATHS)
        and not hosted_workflows
    )
    return _check(passed, "none" if passed else "frozen_split_opened")


def _private_detail_leaks(payloads: list[dict[str, Any]]) -> int:
    prefixes = (
        "/Users/",
        "/home/",
        "/private/var/",
        "/private/tmp/",
        "/tmp/",
        "C:\\Users\\",
    )
    secrets = ("api_key", "token=", "password=", "secret=")
    secret_keys = ("api_key", "token", "password", "secret")
    secret_values = ("sk-", "ghp_", "bearer ")
    leaks = 0

    def visit(value: object) -> None:
        nonlocal leaks
        if isinstance(value, dict):
            for key, item in value.items():
                if any(secret in str(key).casefold() for secret in secret_keys) and item:
                    leaks += 1
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            lowered = value.casefold()
            if (
                any(prefix.casefold() in lowered for prefix in prefixes)
                or any(secret in lowered for secret in secrets)
                or any(secret in lowered for secret in secret_values)
            ):
                leaks += 1

    for payload in payloads:
        visit(payload)
    return leaks


def _load_frozen_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = (
        (DEVELOPMENT, DEVELOPMENT_SHA256, "development"),
        (CONFIRMATION, CONFIRMATION_SHA256, "confirmation"),
        (HOLDOUT, HOLDOUT_SHA256, "holdout"),
    )
    payloads = []
    for path, expected_sha256, split in paths:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError(f"frozen artifact SHA256 mismatch: {path.relative_to(REPO_ROOT)}")
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        if (
            payload.get("schema_version") != 1
            or payload.get("suite_id") != "release-candidate-delivery"
            or payload.get("suite_revision") != 1
            or payload.get("split") != split
            or (split != "development" and payload.get("status") != "not_run")
        ):
            raise ValueError(f"invalid release-candidate {split} manifest")
        payloads.append(payload)
    development, confirmation, holdout = payloads
    case_ids = [case.get("id") for case in development.get("cases", [])]
    if len(case_ids) != 6 or len(case_ids) != len(set(case_ids)):
        raise ValueError("release-candidate development cases changed")
    return development, confirmation, holdout


def _check(passed: bool, classification: str) -> dict[str, object]:
    return {"passed": passed, "error_classification": classification}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cli_version() -> str:
    environment = _clean_python_environment()
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", "--version"],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_module_version() -> str:
    environment = _clean_python_environment()
    completed = subprocess.run(
        [sys.executable, "-c", "import memoryforge; print(memoryforge.__version__)"],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _clean_python_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
        and not key.startswith(("COV_CORE_", "COVERAGE_"))
    }
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _release_artifact_evidence(
    release_dir: Path,
    memoryforge_commit: str,
) -> dict[str, object]:
    provenance = _load_json(release_dir / "release-provenance.json")
    return {
        "package": provenance.get("package"),
        "builds": provenance.get("builds"),
        "sha256sums_sha256": _sha256_if_file(release_dir / "SHA256SUMS"),
        "provenance_sha256": _sha256_if_file(release_dir / "release-provenance.json"),
        "benchmark_summary_sha256": _sha256_if_file(release_dir / "benchmark-summary.json"),
        "workspace_drill_sha256": _sha256_if_file(release_dir / "workspace-drill.json"),
        "artifact_root": (
            f"demo/results/artifacts/release_candidate_development/{memoryforge_commit}"
        ),
    }


def _sha256_if_file(path: Path) -> str:
    return _sha256(path) if path.is_file() else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
