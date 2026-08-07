#!/usr/bin/env python3
"""Run the isolated public Workspace release drill."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src"


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
        "What is the signature of src.service.cache_ttl?",
        "--debug",
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

    showcase_evidence = workdir / "showcase-evidence.json"
    showcase_evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sample_query": {
                    "question": "What is the signature of src.service.cache_ttl?",
                    "answer": query["answer"],
                    "citations": query["citations"],
                    "trace": query.get("trace", []),
                },
                "evaluation": {
                    "suite": "release Workspace drill",
                    "case_count": 1,
                    "memoryforge": {
                        "answer_accuracy": 100.0,
                        "citation_grounding_accuracy": 100.0,
                        "abstention_accuracy": 100.0,
                    },
                    "cases": [],
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

    workspace_commit = _git_output(workspace, "rev-parse", "HEAD")
    backup = workdir / "backup"
    restored = workdir / "restored"
    shutil.copytree(workspace, backup)
    shutil.copytree(backup, restored)
    restored_commit = _git_output(restored, "rev-parse", "HEAD")
    restored_lint = _cli_json("lint", "--workspace", str(restored))
    restored_query = _cli_json(
        "ask",
        "What is the signature of src.service.cache_ttl?",
        "--verify",
        "--repository",
        repository_id,
        "--workspace",
        str(restored),
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
            if query["status"] == restored_query["status"] == "answered"
            and query["citations"]
            and restored_query["citations"]
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
        "schema_version": 1,
        "memoryforge_commit": source_commit,
        "checks": checks,
        "private_detail_leaks": private_detail_leaks,
        "passed": passed,
    }


def _showcase_private_detail_leaks(
    root: Path,
    *,
    forbidden_paths: tuple[str, ...] = (),
) -> int:
    prefixes = ("/Users/", "/home/", "/private/var/", "C:\\Users\\", *forbidden_paths)
    secrets = ("api_key=", "token=", "password=", "secret=", "sk-", "ghp_", "bearer ")
    leaks = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        lowered = text.casefold()
        leaks += sum(prefix in text for prefix in prefixes if prefix)
        leaks += sum(secret in lowered for secret in secrets)
    return leaks


def _cli(*args: str) -> str:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
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
