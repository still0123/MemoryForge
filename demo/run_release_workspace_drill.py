#!/usr/bin/env python3
"""Run the isolated public Workspace release drill."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src"
SIGNATURE_QUESTION = "What is the signature of src.service.cache_ttl?"
EXPECTED_SIGNATURE = "`src.service.cache_ttl` (function): `def cache_ttl() -> int:`"
UNKNOWN_QUESTION = "What is the signature of src.service.missing_symbol?"
_VALIDATOR_PATH = REPO_ROOT / "demo/validate_benchmark_registry.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_benchmark_registry",
    _VALIDATOR_PATH,
)
if _VALIDATOR_SPEC is None or _VALIDATOR_SPEC.loader is None:
    raise RuntimeError("could not load benchmark registry validator")
registry_validator = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(registry_validator)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.is_relative_to(REPO_ROOT) or output.is_relative_to(REPO_ROOT):
        raise SystemExit("release drill paths must remain outside the repository")
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    if output.exists():
        raise SystemExit(f"--output already exists: {output}")
    workdir.mkdir(parents=True, exist_ok=True)

    evidence = run_drill(workdir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote Workspace release drill to {output}")
    if not evidence["passed"]:
        raise SystemExit("Workspace release drill failed")


def run_drill(workdir: Path) -> dict[str, Any]:
    source_commit, source_dirty = _source_identity()
    if source_dirty:
        raise RuntimeError("Workspace release drill requires a clean source worktree")
    sys.path.insert(0, str(SOURCE_ROOT))
    from memoryforge.showcase import build_showcase

    source = workdir / "source"
    source.mkdir()
    source_code = source / "src" / "service.py"
    source_code.parent.mkdir()
    source_code.write_text(
        "CACHE_TTL = 60\n\n\ndef cache_ttl() -> int:\n    return CACHE_TTL\n",
        encoding="utf-8",
    )
    _git(source, "init")
    _git(source, "config", "user.email", "release@example.invalid")
    _git(source, "config", "user.name", "MemoryForge Release")
    _git(source, "remote", "add", "origin", "https://example.invalid/release-fixture.git")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "release fixture")
    fixture_commit = _git_output(source, "rev-parse", "HEAD")

    workspace = workdir / "workspace"
    _cli("init", str(workspace))
    registration = _cli_json(
        "git-add",
        str(source),
        "--public",
        "--workspace",
        str(workspace),
    )
    repository_id = registration["repository_id"]
    _cli_json("git-sync", repository_id, "--workspace", str(workspace))
    _cli_json("code-add", repository_id, "src", "--workspace", str(workspace))
    _cli_json("git-sync", repository_id, "--workspace", str(workspace))
    proposal = _cli_json(
        "ingest",
        "--code-wiki",
        repository_id,
        "--workspace",
        str(workspace),
    )
    changeset_id = proposal["changeset_id"]
    _cli_json("review", changeset_id, "--workspace", str(workspace))
    _cli_json("approve", changeset_id, "--workspace", str(workspace))
    applied = _cli_json("apply", changeset_id, "--workspace", str(workspace))
    lint = _cli_json("lint", "--workspace", str(workspace))
    query = _cli_json(
        "ask",
        SIGNATURE_QUESTION,
        "--debug",
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(workspace),
    )
    unknown = _cli_json(
        "ask",
        UNKNOWN_QUESTION,
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(workspace),
    )
    refresh = _cli_json("refresh", "--workspace", str(workspace))
    no_pending = _cli_json(
        "ingest",
        "--code-wiki",
        repository_id,
        "--workspace",
        str(workspace),
    )

    workspace_commit = _git_output(workspace, "rev-parse", "HEAD")
    backup = workdir / "backup"
    restored = workdir / "restored"
    shutil.copytree(workspace, backup)
    shutil.copytree(backup, restored)
    restored_commit = _git_output(restored, "rev-parse", "HEAD")
    restored_lint = _cli_json("lint", "--workspace", str(restored))
    restored_query = _cli_json(
        "ask",
        SIGNATURE_QUESTION,
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(restored),
    )
    restored_unknown = _cli_json(
        "ask",
        UNKNOWN_QUESTION,
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(restored),
    )
    answered_valid = _answered_query_valid(_replay_payload(query))
    unknown_valid = _unknown_query_valid(_replay_payload(unknown))
    answered_replayed = _replay_payload(query) == _replay_payload(restored_query)
    unknown_replayed = _replay_payload(unknown) == _replay_payload(restored_unknown)
    answered_passed = answered_valid and answered_replayed
    unknown_passed = unknown_valid and unknown_replayed

    cases = [
        {
            "id": "exact-code-signature",
            "category": "exact_symbol",
            "error_classification": ("none" if answered_passed else "answer_or_replay_mismatch"),
            "memoryforge": {
                "answer_correct": answered_passed,
                "abstention_correct": False,
            },
        },
        {
            "id": "unsupported-symbol-abstention",
            "category": "unanswerable",
            "error_classification": ("none" if unknown_passed else "abstention_or_replay_mismatch"),
            "memoryforge": {
                "answer_correct": unknown_passed,
                "abstention_correct": unknown_passed,
            },
        },
    ]
    showcase_evidence = workdir / "showcase-evidence.json"
    showcase_evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sample_query": {
                    "question": SIGNATURE_QUESTION,
                    "answer": query["answer"],
                    "citations": query["citations"],
                    "trace": query.get("trace", []),
                },
                "evaluation": {
                    "suite": "release Workspace drill",
                    "case_count": len(cases),
                    "memoryforge": _evaluation_metrics(answered_passed, unknown_passed),
                    "cases": cases,
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    showcase_root = workdir / "showcase"
    showcase = build_showcase(
        workspace,
        showcase_root,
        evidence=showcase_evidence,
    )
    private_detail_leaks = _showcase_private_detail_leaks(
        showcase_root,
        forbidden_paths=(str(workdir.resolve()),),
    )

    checks = {
        "refresh": "passed" if refresh["status"] == "unchanged" else "failed",
        "review": "passed",
        "approve": "passed",
        "apply": "passed" if applied["status"] == "APPLIED" else "failed",
        "lint": "passed" if lint["status"] == "clean" else "failed",
        "no_pending_ingest": (
            "passed"
            if no_pending == {"status": "up_to_date", "repository_id": repository_id}
            else "failed"
        ),
        "backup": "passed" if backup.is_dir() else "failed",
        "restore": "passed" if restored_commit == workspace_commit else "failed",
        "query": (
            "passed"
            if answered_valid and unknown_valid and answered_replayed and unknown_replayed
            else "failed"
        ),
        "showcase": (
            "passed"
            if showcase["status"] == "built"
            and (showcase_root / "index.html").is_file()
            and private_detail_leaks == 0
            else "failed"
        ),
    }
    passed = (
        all(value == "passed" for value in checks.values()) and restored_lint["status"] == "clean"
    )
    final_commit, final_dirty = _source_identity()
    if final_commit != source_commit or final_dirty:
        raise RuntimeError("Workspace release drill changed the source Commit or worktree")
    return {
        "schema_version": 2,
        "memoryforge_commit": source_commit,
        "fixture": {
            "repository_commit": fixture_commit,
            "repository_id": repository_id,
            "source_id": query["source_id"],
        },
        "checks": checks,
        "evaluation": {
            "metrics": _evaluation_metrics(answered_passed, unknown_passed),
            "cases": cases,
        },
        "queries": {
            "answered": {
                "original": _replay_payload(query),
                "restored": _replay_payload(restored_query),
            },
            "unknown": {
                "original": _replay_payload(unknown),
                "restored": _replay_payload(restored_unknown),
            },
        },
        "workspace": {
            "original_commit": workspace_commit,
            "restored_commit": restored_commit,
            "restored_lint": restored_lint,
        },
        "private_detail_leaks": private_detail_leaks,
        "passed": passed,
    }


def _answered_query_valid(payload: dict[str, Any]) -> bool:
    return registry_validator._release_drill_answered_query(payload)


def _unknown_query_valid(payload: dict[str, Any]) -> bool:
    return registry_validator._release_drill_unknown_query(payload)


def _replay_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "trace"}


def _evaluation_metrics(
    answered_passed: bool,
    unknown_passed: bool,
) -> dict[str, float]:
    return {
        "answer_accuracy": 100.0 if answered_passed else 0.0,
        "citation_grounding_accuracy": 100.0 if answered_passed else 0.0,
        "abstention_accuracy": 100.0 if unknown_passed else 0.0,
    }


def _showcase_private_detail_leaks(
    root: Path,
    *,
    forbidden_paths: tuple[str, ...] = (),
) -> int:
    prefixes = (
        "/Users/",
        "/home/",
        "/private/var/",
        "/private/tmp/",
        "/tmp/",
        "C:\\Users\\",
        *forbidden_paths,
    )
    secrets = ("api_key=", "token=", "password=", "secret=", "sk-", "ghp_", "bearer ")
    leaks = 0

    def strings(value: object) -> list[str]:
        if isinstance(value, dict):
            return [text for key, item in value.items() for text in (str(key), *strings(item))]
        if isinstance(value, list):
            return [text for item in value for text in strings(item)]
        return [value] if isinstance(value, str) else []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        values = [text]
        if path.suffix == ".json":
            with contextlib.suppress(json.JSONDecodeError):
                values = strings(json.loads(text))
        for value in values:
            lowered = value.casefold()
            leaks += sum(prefix.casefold() in lowered for prefix in prefixes if prefix)
            leaks += sum(secret in lowered for secret in secrets)
    return leaks


def _cli(*args: str) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
        and not key.startswith(("COV_CORE_", "COVERAGE_"))
    }
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["SOURCE_DATE_EPOCH"] = "1767225600"
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", *args],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"memoryforge {' '.join(args)} failed:\n{completed.stderr.strip()}")
    return completed.stdout


def _cli_json(*args: str) -> dict[str, Any]:
    payload = json.loads(_cli(*args))
    if not isinstance(payload, dict):
        raise RuntimeError("memoryforge CLI returned a non-object payload")
    return payload


def _git(root: Path, *args: str) -> None:
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    subprocess.run(
        ["git", *args],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
    )


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity() -> tuple[str, bool]:
    return (
        _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        bool(_git_output(REPO_ROOT, "status", "--porcelain")),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
